build-OcrFunction build-OcrPriorityFunction:
	uv run --no-project --with pip python -m pip install --platform manylinux2014_aarch64 --python-version 3.12 --implementation cp --only-binary=:all: -r ocr_worker/requirements.txt -t "$(ARTIFACTS_DIR)"
	cp ocr_worker/handler.py "$(ARTIFACTS_DIR)/handler.py"
	cp -R shared "$(ARTIFACTS_DIR)/shared"
