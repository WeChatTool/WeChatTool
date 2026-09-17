.PHONY: build test analyze prepare
PYTHON ?= python3

build:
	./scripts/build-plugin.sh

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) tests/native_runtime.py

analyze:
	$(PYTHON) -m wechattool analyze

prepare: build
	$(PYTHON) -m wechattool prepare --output build/WeChatTool-WeChat.app
