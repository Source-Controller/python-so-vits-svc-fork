from __future__ import annotations

import json
from logging import getLogger
from pathlib import Path

import PySimpleGUI as sg
import sounddevice as sd
import soundfile as sf
import torch
from pebble import ProcessFuture, ProcessPool
from tqdm.tk import tqdm_tk

from .utils import ensure_hubert_model

GUI_DEFAULT_PRESETS_PATH = Path(__file__).parent / "default_gui_presets.json"
GUI_PRESETS_PATH = Path("./user_gui_presets.json").absolute()

LOG = getLogger(__name__)


def play_audio(path: Path | str):
    if isinstance(path, Path):
        path = path.as_posix()
    data, sr = sf.read(path)
    sd.play(data, sr)


def load_presets() -> dict:
    defaults = json.loads(GUI_DEFAULT_PRESETS_PATH.read_text())
    users = (
        json.loads(GUI_PRESETS_PATH.read_text()) if GUI_PRESETS_PATH.exists() else {}
    )
    # prioriy: defaults > users
    # order: defaults -> users
    return {**defaults, **users, **defaults}


def add_preset(name: str, preset: dict) -> dict:
    presets = load_presets()
    presets[name] = preset
    with GUI_PRESETS_PATH.open("w") as f:
        json.dump(presets, f, indent=2)
    return load_presets()


def delete_preset(name: str) -> dict:
    presets = load_presets()
    if name in presets:
        del presets[name]
    else:
        LOG.warning(f"Cannot delete preset {name} because it does not exist.")
    with GUI_PRESETS_PATH.open("w") as f:
        json.dump(presets, f, indent=2)
    return load_presets()


def get_devices(
    update: bool = True,
) -> tuple[list[str], list[str], list[int], list[int]]:
    if update:
        sd._terminate()
        sd._initialize()
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    for hostapi in hostapis:
        for device_idx in hostapi["devices"]:
            devices[device_idx]["hostapi_name"] = hostapi["name"]
    input_devices = [
        f"{d['name']} ({d['hostapi_name']})"
        for d in devices
        if d["max_input_channels"] > 0
    ]
    output_devices = [
        f"{d['name']} ({d['hostapi_name']})"
        for d in devices
        if d["max_output_channels"] > 0
    ]
    input_devices_indices = [d["index"] for d in devices if d["max_input_channels"] > 0]
    output_devices_indices = [
        d["index"] for d in devices if d["max_output_channels"] > 0
    ]
    return input_devices, output_devices, input_devices_indices, output_devices_indices


def main():
    try:
        ensure_hubert_model(tqdm_cls=tqdm_tk)
    except Exception as e:
        LOG.exception(e)
        LOG.info("Trying tqdm.std...")
        try:
            ensure_hubert_model()
        except Exception as e:
            LOG.exception(e)
            try:
                ensure_hubert_model(disable=True)
            except Exception as e:
                LOG.exception(e)
                LOG.error(
                    "Failed to download Hubert model. Please download it manually."
                )
                return

    sg.theme("Dark")
    model_candidates = list(sorted(Path("./logs/44k/").glob("G_*.pth")))

    frame_contents = {
        "Paths": [
            [
                sg.Text("Model path"),
                sg.Push(),
                sg.InputText(
                    key="model_path",
                    default_text=model_candidates[-1].absolute().as_posix()
                    if model_candidates
                    else "",
                    enable_events=True,
                ),
                sg.FileBrowse(
                    initial_folder=Path("./logs/44k/").absolute
                    if Path("./logs/44k/").exists()
                    else Path(".").absolute().as_posix(),
                    key="model_path_browse",
                    file_types=(("PyTorch", "*.pth"),),
                ),
            ],
            [
                sg.Text("Config path"),
                sg.Push(),
                sg.InputText(
                    key="config_path",
                    default_text=Path("./configs/44k/config.json").absolute().as_posix()
                    if Path("./configs/44k/config.json").exists()
                    else "",
                    enable_events=True,
                ),
                sg.FileBrowse(
                    initial_folder=Path("./configs/44k/").as_posix()
                    if Path("./configs/44k/").exists()
                    else Path(".").absolute().as_posix(),
                    key="config_path_browse",
                    file_types=(("JSON", "*.json"),),
                ),
            ],
            [
                sg.Text("Cluster model path (Optional)"),
                sg.Push(),
                sg.InputText(
                    key="cluster_model_path",
                    default_text=Path("./logs/44k/kmeans.pt").absolute().as_posix()
                    if Path("./logs/44k/kmeans.pt").exists()
                    else "",
                    enable_events=True,
                ),
                sg.FileBrowse(
                    initial_folder="./logs/44k/"
                    if Path("./logs/44k/").exists()
                    else ".",
                    key="cluster_model_path_browse",
                    file_types=(("PyTorch", "*.pt"),),
                ),
            ],
        ],
        "Common": [
            [
                sg.Text("Speaker"),
                sg.Push(),
                sg.Combo(values=[], key="speaker", size=(20, 1)),
            ],
            [
                sg.Text("Silence threshold"),
                sg.Push(),
                sg.Slider(
                    range=(-60.0, 0),
                    orientation="h",
                    key="silence_threshold",
                    resolution=0.1,
                ),
            ],
            [
                sg.Text(
                    "Pitch (12 = 1 octave)\n"
                    "ADJUST THIS based on your voice\n"
                    "when Auto predict F0 is turned off.",
                    size=(None, 4),
                ),
                sg.Push(),
                sg.Slider(
                    range=(-36, 36),
                    orientation="h",
                    key="transpose",
                    tick_interval=12,
                ),
            ],
            [
                sg.Checkbox(
                    key="auto_predict_f0",
                    text="Auto predict F0 (Pitch may become unstable when turned on in real-time inference.)",
                )
            ],
            [
                sg.Text("F0 prediction method"),
                sg.Push(),
                sg.Combo(
                    ["crepe", "crepe-tiny", "parselmouth", "dio", "harvest"],
                    key="f0_method",
                ),
            ],
            [
                sg.Text("Cluster infer ratio"),
                sg.Push(),
                sg.Slider(
                    range=(0, 1.0),
                    orientation="h",
                    key="cluster_infer_ratio",
                    resolution=0.01,
                ),
            ],
            [
                sg.Text("Noise scale"),
                sg.Push(),
                sg.Slider(
                    range=(0.0, 1.0),
                    orientation="h",
                    key="noise_scale",
                    resolution=0.01,
                ),
            ],
            [
                sg.Text("Pad seconds"),
                sg.Push(),
                sg.Slider(
                    range=(0.0, 1.0),
                    orientation="h",
                    key="pad_seconds",
                    resolution=0.01,
                ),
            ],
            [
                sg.Text("Chunk seconds"),
                sg.Push(),
                sg.Slider(
                    range=(0.0, 3.0),
                    orientation="h",
                    key="chunk_seconds",
                    resolution=0.01,
                ),
            ],
            [
                sg.Checkbox(
                    key="absolute_thresh",
                    text="Absolute threshold (ignored (True) in realtime inference)",
                )
            ],
        ],
        "File": [
            [
                sg.Text("Input audio path"),
                sg.Push(),
                sg.InputText(key="input_path"),
                sg.FileBrowse(initial_folder=".", key="input_path_browse"),
                sg.Button("Play", key="play_input"),
            ],
            [sg.Checkbox(key="auto_play", text="Auto play", default=True)],
        ],
        "Realtime": [
            [
                sg.Text("Crossfade seconds"),
                sg.Push(),
                sg.Slider(
                    range=(0, 0.6),
                    orientation="h",
                    key="crossfade_seconds",
                    resolution=0.001,
                ),
            ],
            [
                sg.Text(
                    "Block seconds",  # \n(big -> more robust, slower, (the same) latency)"
                    tooltip="Big -> more robust, slower, (the same) latency",
                ),
                sg.Push(),
                sg.Slider(
                    range=(0, 3.0),
                    orientation="h",
                    key="block_seconds",
                    resolution=0.001,
                ),
            ],
            [
                sg.Text(
                    "Additional Infer seconds (before)",  # \n(big -> more robust, slower)"
                    tooltip="Big -> more robust, slower, additional latency",
                ),
                sg.Push(),
                sg.Slider(
                    range=(0, 2.0),
                    orientation="h",
                    key="additional_infer_before_seconds",
                    resolution=0.001,
                ),
            ],
            [
                sg.Text(
                    "Additional Infer seconds (after)",  # \n(big -> more robust, slower, additional latency)"
                    tooltip="Big -> more robust, slower, additional latency",
                ),
                sg.Push(),
                sg.Slider(
                    range=(0, 2.0),
                    orientation="h",
                    key="additional_infer_after_seconds",
                    resolution=0.001,
                ),
            ],
            [
                sg.Text("Realtime algorithm"),
                sg.Push(),
                sg.Combo(
                    ["2 (Divide by speech)", "1 (Divide constantly)"],
                    default_value="1 (Divide constantly)",
                    key="realtime_algorithm",
                ),
            ],
            [
                sg.Text("Input device"),
                sg.Push(),
                sg.Combo(
                    key="input_device",
                    values=[],
                    size=(60, 1),
                ),
            ],
            [
                sg.Text("Output device"),
                sg.Push(),
                sg.Combo(
                    key="output_device",
                    values=[],
                    size=(60, 1),
                ),
            ],
            [
                sg.Checkbox(
                    "Passthrough original audio (for latency check)",
                    key="passthrough_original",
                    default=False,
                ),
                sg.Push(),
                sg.Button("Refresh devices", key="refresh_devices"),
            ],
            [
                sg.Frame(
                    "Notes",
                    [
                        [
                            sg.Text(
                                "In Realtime Inference:\n"
                                "    - Setting F0 prediction method to 'crepe` may cause performance degradation.\n"
                                "    - Auto Predict F0 must be turned off.\n"
                                "If the audio sounds mumbly and choppy:\n"
                                "    Case: The inference has not been made in time (Increase Block seconds)\n"
                                "    Case: Mic input is low (Decrease Silence threshold)\n"
                            )
                        ]
                    ],
                ),
            ],
        ],
        "Presets": [
            [
                sg.Text("Presets"),
                sg.Push(),
                sg.Combo(
                    key="presets",
                    values=list(load_presets().keys()),
                    size=(20, 1),
                    enable_events=True,
                ),
                sg.Button("Delete preset", key="delete_preset"),
            ],
            [
                sg.Text("Preset name"),
                sg.Stretch(),
                sg.InputText(key="preset_name", size=(20, 1)),
                sg.Button("Add current settings as a preset", key="add_preset"),
            ],
        ],
    }

    # frames
    frames = {}
    for name, items in frame_contents.items():
        frame = sg.Frame(name, items)
        frame.expand_x = True
        frames[name] = [frame]

    column1 = sg.Column(
        [
            frames["Paths"],
            frames["Common"],
        ],
        vertical_alignment="top",
    )
    column2 = sg.Column(
        [
            frames["File"],
            frames["Realtime"],
            frames["Presets"],
            [
                sg.Checkbox(
                    key="use_gpu",
                    default=(
                        torch.cuda.is_available() or torch.backends.mps.is_available()
                    ),
                    text="Use GPU"
                    + (
                        " (not available; if your device has GPU, make sure you installed PyTorch with CUDA support)"
                        if not (
                            torch.cuda.is_available()
                            or torch.backends.mps.is_available()
                        )
                        else ""
                    ),
                    disabled=not (
                        torch.cuda.is_available() or torch.backends.mps.is_available()
                    ),
                )
            ],
            [
                sg.Button("Infer", key="infer"),
                sg.Button("(Re)Start Voice Changer", key="start_vc"),
                sg.Button("Stop Voice Changer", key="stop_vc"),
                sg.Push(),
                sg.Button("ONNX Export", key="onnx_export"),
            ],
        ]
    )

    # columns
    layout = [[column1, column2]]
    # layout = [[sg.Column(layout, vertical_alignment="top", scrollable=True, expand_x=True, expand_y=True)]]
    window = sg.Window(
        f"{__name__.split('.')[0]}", layout, grab_anywhere=True, finalize=True
    )  # , use_custom_titlebar=True)
    # for n in ["input_device", "output_device"]:
    #     window[n].Widget.configure(justify="right")
    event, values = window.read(timeout=0.01)

    def update_speaker() -> None:
        from . import utils

        config_path = Path(values["config_path"])
        if config_path.exists() and config_path.is_file():
            hp = utils.get_hparams_from_file(values["config_path"])
            LOG.debug(f"Loaded config from {values['config_path']}")
            window["speaker"].update(
                values=list(hp.__dict__["spk"].keys()), set_to_index=0
            )

    def update_devices() -> None:
        input_devices, output_devices, _, _ = get_devices()
        window["input_device"].update(
            values=input_devices, value=values["input_device"]
        )
        window["output_device"].update(
            values=output_devices, value=values["output_device"]
        )
        input_default, output_default = sd.default.device
        if values["input_device"] not in input_devices:
            window["input_device"].update(
                values=input_devices,
                set_to_index=0 if input_default is None else input_default - 1,
            )
        if values["output_device"] not in output_devices:
            window["output_device"].update(
                values=output_devices,
                set_to_index=0 if output_default is None else output_default - 1,
            )

    PRESET_KEYS = [
        key
        for key in values.keys()
        if not any(exclude in key for exclude in ["preset", "browse"])
    ]

    def apply_preset(name: str) -> None:
        for key, value in load_presets()[name].items():
            if key in PRESET_KEYS:
                window[key].update(value)
                values[key] = value

    default_name = list(load_presets().keys())[0]
    apply_preset(default_name)
    window["presets"].update(default_name)
    del default_name
    update_speaker()
    update_devices()
    with ProcessPool(max_workers=1) as pool:
        future: None | ProcessFuture = None
        while True:
            event, values = window.read(200)
            if event == sg.WIN_CLOSED:
                break
            if not event == sg.EVENT_TIMEOUT:
                LOG.info(f"Event {event}, values {values}")
            if event.endswith("_path"):
                for name in window.AllKeysDict:
                    if str(name).endswith("_browse"):
                        browser = window[name]
                        if isinstance(browser, sg.Button):
                            LOG.info(
                                f"Updating browser {browser} to {Path(values[event]).parent}"
                            )
                            browser.InitialFolder = Path(values[event]).parent
                            browser.update()
                        else:
                            LOG.warning(f"Browser {browser} is not a FileBrowse")
            window["transpose"].update(
                disabled=values["auto_predict_f0"],
                visible=not values["auto_predict_f0"],
            )
            if event == "add_preset":
                presets = add_preset(
                    values["preset_name"], {key: values[key] for key in PRESET_KEYS}
                )
                window["presets"].update(values=list(presets.keys()))
            elif event == "delete_preset":
                presets = delete_preset(values["presets"])
                window["presets"].update(values=list(presets.keys()))
            elif event == "presets":
                apply_preset(values["presets"])
                update_speaker()
            elif event == "refresh_devices":
                update_devices()
            elif event == "config_path":
                update_speaker()
            elif event == "infer":
                from .inference_main import infer

                input_path = Path(values["input_path"])
                output_path = (
                    input_path.parent / f"{input_path.stem}.out{input_path.suffix}"
                )
                if not input_path.exists() or not input_path.is_file():
                    LOG.warning(f"Input path {input_path} does not exist.")
                    continue
                try:
                    infer(
                        # paths
                        model_path=Path(values["model_path"]),
                        output_path=output_path,
                        input_path=input_path,
                        config_path=Path(values["config_path"]),
                        # svc config
                        speaker=values["speaker"],
                        cluster_model_path=Path(values["cluster_model_path"])
                        if values["cluster_model_path"]
                        else None,
                        transpose=values["transpose"],
                        auto_predict_f0=values["auto_predict_f0"],
                        cluster_infer_ratio=values["cluster_infer_ratio"],
                        noise_scale=values["noise_scale"],
                        f0_method=values["f0_method"],
                        # slice config
                        db_thresh=values["silence_threshold"],
                        pad_seconds=values["pad_seconds"],
                        chunk_seconds=values["chunk_seconds"],
                        absolute_thresh=values["absolute_thresh"],
                        device="cpu"
                        if not values["use_gpu"]
                        else (
                            "cuda"
                            if torch.cuda.is_available()
                            else "mps"
                            if torch.backends.mps.is_available()
                            else "cpu"
                        ),
                    )
                    if values["auto_play"]:
                        pool.schedule(play_audio, args=[output_path])
                except Exception as e:
                    LOG.exception(e)
            elif event == "play_input":
                if Path(values["input_path"]).exists():
                    pool.schedule(play_audio, args=[Path(values["input_path"])])
            elif event == "start_vc":
                _, _, input_device_indices, output_device_indices = get_devices(
                    update=False
                )
                from .inference_main import realtime

                if future:
                    LOG.info("Canceling previous task")
                    future.cancel()
                future = pool.schedule(
                    realtime,
                    kwargs=dict(
                        # paths
                        model_path=Path(values["model_path"]),
                        config_path=Path(values["config_path"]),
                        speaker=values["speaker"],
                        # svc config
                        cluster_model_path=Path(values["cluster_model_path"])
                        if values["cluster_model_path"]
                        else None,
                        transpose=values["transpose"],
                        auto_predict_f0=values["auto_predict_f0"],
                        cluster_infer_ratio=values["cluster_infer_ratio"],
                        noise_scale=values["noise_scale"],
                        f0_method=values["f0_method"],
                        # slice config
                        db_thresh=values["silence_threshold"],
                        pad_seconds=values["pad_seconds"],
                        chunk_seconds=values["chunk_seconds"],
                        # realtime config
                        crossfade_seconds=values["crossfade_seconds"],
                        additional_infer_before_seconds=values[
                            "additional_infer_before_seconds"
                        ],
                        additional_infer_after_seconds=values[
                            "additional_infer_after_seconds"
                        ],
                        block_seconds=values["block_seconds"],
                        version=int(values["realtime_algorithm"][0]),
                        input_device=input_device_indices[
                            window["input_device"].widget.current()
                        ],
                        output_device=output_device_indices[
                            window["output_device"].widget.current()
                        ],
                        device="cuda" if values["use_gpu"] else "cpu",
                        passthrough_original=values["passthrough_original"],
                    ),
                )
            elif event == "stop_vc":
                if future:
                    future.cancel()
                    future = None
            elif event == "onnx_export":
                from .onnx_export import onnx_export

                try:
                    onnx_export(
                        input_path=Path(values["model_path"]),
                        output_path=Path(values["model_path"]).with_suffix(".onnx"),
                        config_path=Path(values["config_path"]),
                        device="cpu",
                    )
                except Exception as e:
                    LOG.exception(e)
            if future is not None and future.done():
                LOG.error("Error in realtime: ")
                try:
                    future.result()
                except Exception as e:
                    LOG.exception(e)
                future = None
        if future:
            future.cancel()
    window.close()
