from __future__ import annotations

import json
import textwrap
from logging import getLogger
from pathlib import Path

import PySimpleGUI as sg
import sounddevice as sd
import soundfile as sf
import torch
from pebble import ProcessPool

from .__main__ import init_logger

GUI_DEFAULT_PRESETS_PATH = Path(__file__).parent / "default_gui_presets.json"
GUI_PRESETS_PATH = Path("./user_gui_presets.json").absolute()
LOG = getLogger(__name__)

init_logger()


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
    return {**defaults, **users}


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


def main():
    sg.theme("Dark")
    model_candidates = list(sorted(Path("./logs/44k/").glob("G_*.pth")))

    devices = sd.query_devices()
    input_devices = [d["name"] for d in devices if d["max_input_channels"] > 0]
    output_devices = [d["name"] for d in devices if d["max_output_channels"] > 0]
    devices[sd.default.device[0]]["name"]
    devices[sd.default.device[1]]["name"]

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
                sg.Text("Cluster model path"),
                sg.Push(),
                sg.InputText(key="cluster_model_path", enable_events=True),
                sg.FileBrowse(
                    initial_folder="./logs/44k/"
                    if Path("./logs/44k/").exists()
                    else ".",
                    key="cluster_model_path_browse",
                    file_types=(("PyTorch", "*.pth"),),
                ),
            ],
        ],
        "Common": [
            [
                sg.Text("Speaker"),
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
                sg.Text("Pitch (12 = 1 octave)"),
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
            [sg.Checkbox(key="auto_play", text="Auto play")],
        ],
        "Realtime": [
            [
                sg.Text(
                    "In Realtime Inference:\n"
                    "    Setting F0 prediction method to 'crepe` may cause performance degradation.\n"
                    "    Auto Predict F0 must be turned off.\n"
                    + textwrap.fill(
                        "If the audio sounds mumbly and choppy, the inference has not been made in time "
                        "and the below parameters should be adjusted or the microphone input is too low and the "
                        "silence threshold should be increased.",
                        80,
                    )
                )
            ],
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
                    "Block seconds (big -> more robust, slower, (the same) latency)"
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
                    "Additional Infer seconds (before) (big -> more robust, slower)"
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
                    "Additional Infer seconds (after) (big -> more robust, slower, additional latency)"
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
                sg.Combo(
                    ["2 (Divide by speech)", "1 (Divide constantly)"],
                    default_value="1 (Divide constantly)",
                    key="realtime_algorithm",
                ),
            ],
            [
                sg.Text("Input device"),
                sg.Combo(
                    key="input_device",
                    values=input_devices,
                    size=(20, 1),
                    default_value=input_devices[0],
                ),
            ],
            [
                sg.Text("Output device"),
                sg.Combo(
                    key="output_device",
                    values=output_devices,
                    size=(20, 1),
                    default_value=output_devices[0],
                ),
            ],
            [
                sg.Checkbox(
                    "Passthrough original audio (for latency check)",
                    key="passthrough_original",
                    default=False,
                ),
            ],
        ],
    }

    layout = []
    for name, items in frame_contents.items():
        frame = sg.Frame(name, items)
        frame.expand_x = True
        layout.append([frame])
    layout.extend(
        [
            [
                sg.Checkbox(
                    key="use_gpu", default=torch.cuda.is_available(), text="Use GPU"
                )
            ],
            [
                sg.Text("Presets"),
                sg.Combo(
                    key="presets",
                    values=list(load_presets().keys()),
                    size=(20, 1),
                    enable_events=True,
                ),
                sg.Button("Delete preset", key="delete_preset"),
                sg.InputText(key="preset_name"),
                sg.Button("Add preset", key="add_preset"),
            ],
            [
                sg.Button("Infer", key="infer"),
                sg.Button("(Re)Start Voice Changer", key="start_vc"),
                sg.Button("Stop Voice Changer", key="stop_vc"),
            ],
        ]
    )
    window = sg.Window(
        f"{__name__.split('.')[0]}", layout
    )  # , use_custom_titlebar=True)

    event, values = window.read(timeout=0.01)

    def update_speaker() -> None:
        from . import utils

        config_path = Path(values["config_path"])
        if config_path.exists() and config_path.is_file():
            hp = utils.get_hparams_from_file(values["config_path"])
            LOG.info(f"Loaded config from {values['config_path']}")
            window["speaker"].update(
                values=list(hp.__dict__["spk"].keys()), set_to_index=0
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

    update_speaker()
    default_name = list(load_presets().keys())[0]
    apply_preset(default_name)
    window["presets"].update(default_name)
    del default_name
    with ProcessPool(max_workers=1) as pool:
        future = None
        while True:
            event, values = window.read()
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
                infer(
                    model_path=Path(values["model_path"]),
                    config_path=Path(values["config_path"]),
                    input_path=input_path,
                    output_path=output_path,
                    speaker=values["speaker"],
                    cluster_model_path=Path(values["cluster_model_path"])
                    if values["cluster_model_path"]
                    else None,
                    transpose=values["transpose"],
                    auto_predict_f0=values["auto_predict_f0"],
                    cluster_infer_ratio=values["cluster_infer_ratio"],
                    noise_scale=values["noise_scale"],
                    db_thresh=values["silence_threshold"],
                    pad_seconds=values["pad_seconds"],
                    absolute_thresh=values["absolute_thresh"],
                    chunk_seconds=values["chunk_seconds"],
                    device="cuda" if values["use_gpu"] else "cpu",
                )
                if values["auto_play"]:
                    pool.schedule(play_audio, args=[output_path])
            elif event == "play_input":
                if Path(values["input_path"]).exists():
                    pool.schedule(play_audio, args=[Path(values["input_path"])])
            elif event == "start_vc":
                from .inference_main import realtime

                if future:
                    LOG.info("Canceling previous task")
                    future.cancel()
                future = pool.schedule(
                    realtime,
                    kwargs=dict(
                        model_path=Path(values["model_path"]),
                        config_path=Path(values["config_path"]),
                        speaker=values["speaker"],
                        cluster_model_path=Path(values["cluster_model_path"])
                        if values["cluster_model_path"]
                        else None,
                        transpose=values["transpose"],
                        auto_predict_f0=values["auto_predict_f0"],
                        cluster_infer_ratio=values["cluster_infer_ratio"],
                        noise_scale=values["noise_scale"],
                        f0_method=values["f0_method"],
                        crossfade_seconds=values["crossfade_seconds"],
                        additional_infer_before_seconds=values[
                            "additional_infer_before_seconds"
                        ],
                        additional_infer_after_seconds=values[
                            "additional_infer_after_seconds"
                        ],
                        db_thresh=values["silence_threshold"],
                        pad_seconds=values["pad_seconds"],
                        chunk_seconds=values["chunk_seconds"],
                        version=int(values["realtime_algorithm"][0]),
                        device="cuda" if values["use_gpu"] else "cpu",
                        block_seconds=values["block_seconds"],
                        input_device=values["input_device"],
                        output_device=values["output_device"],
                        passthrough_original=values["passthrough_original"],
                    ),
                )
            elif event == "stop_vc":
                if future:
                    future.cancel()
                    future = None
        if future:
            future.cancel()
    window.close()
