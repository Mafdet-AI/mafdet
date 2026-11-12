import pytest


@pytest.fixture(autouse=True)
def mafdet_home(tmp_path, monkeypatch):
    """Every test gets its own empty ~/.mafdet: no settings, measurements or models of the real user."""
    monkeypatch.setenv("MAFDET_HOME", str(tmp_path / "mafdet-home"))
    return tmp_path / "mafdet-home"


@pytest.fixture(autouse=True)
def hf_home(tmp_path, monkeypatch):
    """And its own empty Hugging Face cache: catalog.local_path also finds a model in the HF cache, so a
    Qwen3-4B the machine happens to hold would count as installed. No token of the real user either.
    huggingface_hub reads its constants from the environment once, at import: they are patched too."""
    import huggingface_hub.constants as hf
    home = tmp_path / "hf-home"
    paths = {"HF_HOME": home, "HF_HUB_CACHE": home / "hub", "HF_XET_CACHE": home / "xet",
             "HF_ASSETS_CACHE": home / "assets", "HF_TOKEN_PATH": home / "token",
             "HF_STORED_TOKENS_PATH": home / "stored_tokens"}
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))                          # for subprocesses
        if hasattr(hf, name):
            monkeypatch.setattr(hf, name, str(path))                 # for this process
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setattr(hf, "HUGGINGFACE_HUB_CACHE", str(paths["HF_HUB_CACHE"]), raising=False)
    return home
