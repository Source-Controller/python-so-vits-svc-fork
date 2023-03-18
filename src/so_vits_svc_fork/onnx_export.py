from __future__ import annotations

from pathlib import Path

import torch

from . import utils
from .onnxexport.model_onnx import SynthesizerTrn


def onnx_export(
    input_path: Path | str,
    output_path: Path | str,
    config_path: Path | str,
    device: str | torch.device = "cpu",
):
    input_path = Path(input_path)
    output_path = Path(output_path)
    config_path = Path(config_path)
    hps = utils.get_hparams_from_file(config_path.as_posix())
    SVCVITS = SynthesizerTrn(
        hps.data.filter_length // 2 + 1,
        hps.train.segment_size // hps.data.hop_length,
        **hps.model,
    )
    _ = utils.load_checkpoint(input_path.as_posix(), SVCVITS, None)
    _ = SVCVITS.eval().to(device)
    for i in SVCVITS.parameters():
        i.requires_grad = False

    test_hidden_unit = torch.rand(1, 10, 256)
    test_pitch = torch.rand(1, 10)
    test_mel2ph = torch.LongTensor([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]).unsqueeze(0)
    test_uv = torch.ones(1, 10, dtype=torch.float32)
    test_noise = torch.randn(1, 192, 10)
    test_sid = torch.LongTensor([0])
    input_names = ["c", "f0", "mel2ph", "uv", "noise", "sid"]
    output_names = [
        "audio",
    ]

    torch.onnx.export(
        SVCVITS,
        (
            test_hidden_unit.to(device),
            test_pitch.to(device),
            test_mel2ph.to(device),
            test_uv.to(device),
            test_noise.to(device),
            test_sid.to(device),
        ),
        output_path.as_posix(),
        dynamic_axes={
            "c": [0, 1],
            "f0": [1],
            "mel2ph": [1],
            "uv": [1],
            "noise": [2],
        },
        do_constant_folding=False,
        opset_version=16,
        verbose=False,
        input_names=input_names,
        output_names=output_names,
    )
