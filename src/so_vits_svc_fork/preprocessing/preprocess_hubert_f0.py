from __future__ import annotations

from logging import getLogger
from pathlib import Path
from random import shuffle
from typing import Iterable, Literal

import librosa
import numpy as np
import torch
import torchaudio
from fairseq.models.hubert import HubertModel
from joblib import Parallel, delayed
from tqdm import tqdm

import so_vits_svc_fork.f0
from so_vits_svc_fork import utils

from ..hparams import HParams
from ..modules.mel_processing import spec_to_mel_torch, spectrogram_torch
from ..utils import get_total_gpu_memory
from .preprocess_utils import check_hubert_min_duration

LOG = getLogger(__name__)
HUBERT_MEMORY = 1600
HUBERT_MEMORY_CREPE = 2600


def _process_one(
    *,
    filepath: Path,
    content_model: HubertModel,
    device: Literal["cuda", "cpu"] = "cuda",
    f0_method: Literal["crepe", "crepe-tiny", "parselmouth", "dio", "harvest"] = "dio",
    force_rebuild: bool = False,
    hps: HParams,
):
    audio, sr = librosa.load(filepath, sr=hps.data.sampling_rate, mono=True)

    if not check_hubert_min_duration(audio, sr):
        LOG.info(f"Skip {filepath} because it is too short.")
        return

    data_path = filepath.parent / (filepath.name + ".data.pt")
    if data_path.exists() and not force_rebuild:
        return

    # Compute f0
    f0 = so_vits_svc_fork.f0.compute_f0(
        audio, sampling_rate=sr, hop_length=hps.data.hop_length, method=f0_method
    )
    f0, uv = so_vits_svc_fork.f0.interpolate_f0(f0)
    f0 = torch.from_numpy(f0).float()
    uv = torch.from_numpy(uv).float()

    # Compute HuBERT content
    audio = torch.from_numpy(audio).float().cuda()
    c = utils.get_content(
        content_model,
        audio,
        device,
        sr=sr,
        legacy_final_proj=hps.data.get("contentvec_final_proj", True),
    )
    c = utils.repeat_expand_2d(c.squeeze(0), f0.shape[0])
    torch.cuda.empty_cache()

    # Compute spectrogram
    audio, sr = torchaudio.load(filepath)
    spec = spectrogram_torch(audio, hps).squeeze(0)
    mel_spec = spec_to_mel_torch(spec, hps)
    torch.cuda.empty_cache()

    # fix lengths
    lmin = min(spec.shape[1], mel_spec.shape[1], f0.shape[0], uv.shape[0], c.shape[1])
    spec, mel_spec, f0, uv, c = (
        spec[:, :lmin],
        mel_spec[:, :lmin],
        f0[:lmin],
        uv[:lmin],
        c[:, :lmin],
    )

    # get speaker id
    spk_name = filepath.parent.name
    spk = hps.spk.__dict__[spk_name]
    spk = torch.tensor(spk).long()
    assert (
        spec.shape[1] == mel_spec.shape[1] == f0.shape[0] == uv.shape[0] == c.shape[1]
    ), (spec.shape, mel_spec.shape, f0.shape, uv.shape, c.shape)
    data = {
        "spec": spec,
        "mel_spec": mel_spec,
        "f0": f0,
        "uv": uv,
        "content": c,
        "audio": audio,
        "spk": spk,
    }
    data = {k: v.cpu() for k, v in data.items()}
    torch.save(data, data_path)


def _process_batch(filepaths: Iterable[Path], pbar_position: int, **kwargs):
    content_model = utils.get_hubert_model("cuda")

    for filepath in tqdm(filepaths, position=pbar_position):
        _process_one(
            content_model=content_model,
            filepath=filepath,
            **kwargs,
        )


def preprocess_hubert_f0(
    input_dir: Path | str,
    config_path: Path | str,
    n_jobs: int | None = None,
    f0_method: Literal["crepe", "crepe-tiny", "parselmouth", "dio", "harvest"] = "dio",
    force_rebuild: bool = False,
):
    input_dir = Path(input_dir)
    config_path = Path(config_path)
    utils.ensure_pretrained_model(".", "contentvec")
    hps = utils.get_hparams(config_path)
    if n_jobs is None:
        memory = get_total_gpu_memory("free")
        n_jobs = (
            memory // (HUBERT_MEMORY_CREPE if f0_method == "crepe" else HUBERT_MEMORY)
            if memory is not None
            else 1
        )
        LOG.info(f"n_jobs automatically set to {n_jobs}, memory: {memory} MiB")

    filepaths = list(input_dir.rglob("*.wav"))
    n_jobs = min(len(filepaths) // 16 + 1, n_jobs)
    shuffle(filepaths)
    filepath_chunks = np.array_split(filepaths, n_jobs)
    Parallel(n_jobs=n_jobs)(
        delayed(_process_batch)(
            filepaths=chunk,
            pbar_position=pbar_position,
            f0_method=f0_method,
            force_rebuild=force_rebuild,
            hps=hps,
        )
        for (pbar_position, chunk) in enumerate(filepath_chunks)
    )
