"""Optional local Clef worker: one JSON request/response per line, no cloud fallback."""
import json
import os
from pathlib import Path
import sys

_model = None


def decide(request):
    global _model
    model_path = Path(os.environ.get("CZIP_CLEF_MODEL", "")).expanduser()
    if not os.environ.get("CZIP_CLEF_MODEL") or not model_path.is_dir():
        raise ValueError("Configure CZIP_CLEF_MODEL with an existing local model directory")
    if _model is None:
        import clef_mlx
        _model = clef_mlx.load(str(model_path))
    questions = {key: (value if isinstance(value, dict) else {"type": "noul", "instructions": value})
                 for key, value in request["questions"].items()}
    state = request.get("state")
    state = state if isinstance(state, dict) else {"context": str(state or "")}
    result = _model.systemone({"model": model_path.name, "state": state, "questions": questions})
    answers, confidence = {}, {}
    for key, answer in result.get("answers", {}).items():
        if key not in questions or not isinstance(answer, dict) or "noul" not in answer:
            continue
        answers[key] = float(answer["noul"])
        confidence[key] = float(answer.get("confidence", max(answers[key], 1 - answers[key])))
    return {"answers": answers, "confidence": confidence, "engine": "clef"}


def main():
    for line in sys.stdin:
        try:
            result = decide(json.loads(line))
        except Exception as error:
            result = {"error": type(error).__name__}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
