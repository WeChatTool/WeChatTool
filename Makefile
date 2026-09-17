.PHONY: build test analyze prepare installer
PYTHON ?= python3

build:
	./scripts/build-plugin.sh

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) tests/native_runtime.py
	$(PYTHON) tests/instance_isolation.py
	$(PYTHON) tests/recall_notice.py
	$(PYTHON) tests/recall_runtime.py
	$(PYTHON) tests/notice_hook.py
	$(PYTHON) tests/notice_profile_validation.py

analyze:
	$(PYTHON) -m wechattool analyze

prepare: build
	$(PYTHON) -m wechattool prepare --output build/WeChatTool-WeChat.app

installer:
	$(PYTHON) scripts/build-installer.py
