"""Optional heavyweight runtime, loaded only by the composition root for NLI."""

from __future__ import annotations

from typing import Any

from decision_router.domain import ErrorCode, ProviderError


class TransformersScorer:
    def __init__(self, model: str, revision: str, *, local_files_only: bool = True) -> None:
        # Transformers/PyTorch expose dynamically typed tensor/model APIs at this seam only.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch: Any = torch
        self._tokenizer: Any = AutoTokenizer.from_pretrained(
            model,
            revision=revision,
            local_files_only=local_files_only,
            trust_remote_code=False,
        )
        self._model: Any = (
            AutoModelForSequenceClassification.from_pretrained(
                model,
                revision=revision,
                local_files_only=local_files_only,
                trust_remote_code=False,
                use_safetensors=True,
            )
            .float()
            .eval()
        )
        labels = {str(k).lower(): int(v) for k, v in self._model.config.label2id.items()}
        if "entailment" not in labels:
            raise ValueError("Model must expose entailment label")
        negative = labels.get("contradiction", labels.get("not_entailment"))
        if negative is None:
            raise ValueError("Model must expose contradiction or not_entailment label")
        self._entailment = labels["entailment"]
        self._negative = negative
        self._max_tokens = min(
            int(self._tokenizer.model_max_length),
            int(self._model.config.max_position_embeddings),
            512,
        )
        torch.set_num_threads(4)

    def score(self, premise: str, hypotheses: list[str]) -> list[tuple[float, float]]:
        features = self._tokenizer(
            [premise] * len(hypotheses),
            hypotheses,
            padding=False,
            truncation=False,
        )
        if any(len(ids) > self._max_tokens for ids in features["input_ids"]):
            raise ProviderError(ErrorCode.UNSUPPORTED)
        results: list[tuple[float, float]] = []
        with self._torch.inference_mode():
            for offset in range(0, len(hypotheses), 8):
                batch = self._tokenizer.pad(
                    {key: value[offset : offset + 8] for key, value in features.items()},
                    padding=True,
                    return_tensors="pt",
                )
                logits = self._model(**batch).logits.tolist()
                results.extend(
                    (float(row[self._entailment]), float(row[self._negative])) for row in logits
                )
        return results
