from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
NIMBUS_GEMMA4_MODEL = "ollama/gemma4:26b"
OLLAMA_GEMMA4_MODEL = "gemma4:26b"


def test_compose_wires_nimbus_to_ollama_gemma4_26b():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())

    nimbus = compose["services"]["nimbus"]
    ollama_pull = compose["services"]["ollama-pull"]

    assert nimbus["environment"]["NIMBUS_MODEL"] == f"${{NIMBUS_MODEL:-{NIMBUS_GEMMA4_MODEL}}}"
    assert nimbus["environment"]["OLLAMA_BASE_URL"] == "http://ollama:11434"
    assert nimbus["environment"]["NIMBUS_API_URL"] == "http://127.0.0.1:4096"
    assert nimbus["build"]["args"]["NODE_IMAGE"] == "${NODE_IMAGE:-node:20-bookworm-slim}"
    assert nimbus["depends_on"]["ollama-pull"]["condition"] == "service_completed_successfully"
    assert compose["services"]["ollama"]["build"]["dockerfile"] == "docker/ollama/Dockerfile"
    assert compose["services"]["ollama"]["build"]["args"]["OLLAMA_BASE_IMAGE"] == "${OLLAMA_BASE_IMAGE:-ubuntu:24.04}"
    assert compose["services"]["ollama"]["build"]["args"]["OLLAMA_VERSION"] == "${OLLAMA_VERSION:-}"
    assert compose["services"]["ollama"]["image"] == "${OLLAMA_IMAGE:-nimbus-ollama:local}"
    ollama_env = compose["services"]["ollama"]["environment"]
    assert ollama_env["NVIDIA_VISIBLE_DEVICES"] == "${OLLAMA_GPU_DEVICE:-1}"
    devices = compose["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices[0]["device_ids"] == ["${OLLAMA_GPU_DEVICE:-1}"]
    assert devices[0]["capabilities"] == ["gpu"]
    assert ollama_pull["image"] == "${OLLAMA_IMAGE:-nimbus-ollama:local}"
    assert ollama_pull["environment"]["OLLAMA_MODEL"] == f"${{OLLAMA_MODEL:-{OLLAMA_GEMMA4_MODEL}}}"


def test_dockerfile_runs_both_runtime_and_webui():
    dockerfile = (ROOT / "Dockerfile").read_text()
    start_script = (ROOT / "docker" / "start.sh").read_text()

    assert "uv sync --frozen --no-dev" in dockerfile
    assert f"NIMBUS_MODEL={NIMBUS_GEMMA4_MODEL}" in dockerfile
    assert "npm run build" in dockerfile
    assert "/healthz" in dockerfile
    assert "nimbus serve" in start_script
    assert "npm run start" in start_script


def test_dev_compose_mounts_backend_source_for_restart_only_iteration():
    compose = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text())

    volumes = compose["services"]["nimbus"]["volumes"]
    assert "./src:/app/src:ro" in volumes


def test_ollama_dockerfile_installs_current_runtime():
    dockerfile = (ROOT / "docker" / "ollama" / "Dockerfile").read_text()

    assert "https://ollama.com/install.sh" in dockerfile
    assert "OLLAMA_VERSION" in dockerfile
    assert 'CMD ["serve"]' in dockerfile
