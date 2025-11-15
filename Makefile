ifneq (,$(wildcard .env))
include .env
export $(shell sed -n 's/^\\([A-Za-z0-9_][A-Za-z0-9_]*\\)=.*$$/\\1/p' .env)
endif

UV ?= uv
PYTHON ?= python
VENV ?= .venv
RUN_ARGS ?=

.PHONY: help venv install run shell clean

.DEFAULT_GOAL := help

help:
	@echo "plaso-downloader helper targets"
	@echo "  make install           # 创建虚拟环境并以开发模式安装（使用 uv）"
	@echo "  make run [RUN_ARGS=...] # 启动下载程序（参数可写在 .env 或 RUN_ARGS）"
	@echo "  make shell             # 打开已激活的虚拟环境 shell"
	@echo "  make clean             # 删除 .venv 和 __pycache__"
	@echo ""
	@echo "示例："
	@echo "  make run RUN_ARGS=\"--list-groups\""
	@echo "  make run RUN_ARGS=\"--group-id 3173947 --all-packages\""
	@echo ""
	@echo "所有常用参数可放到 .env，程序会自动读取。"
	@echo "当前配置：VENV=$(VENV)  UV=$(UV)  PYTHON=$(PYTHON)"

venv:
	@echo "[venv] 使用 $(UV) venv 创建/更新 $(VENV)"
	$(UV) venv $(VENV)

install: venv
	@echo "[install] 使用 $(UV) pip install -e ."
	$(UV) pip install -r requirements.txt

run:
	@if [ ! -f "$(VENV)/bin/activate" ]; then \
	  echo "[run] 未检测到 $(VENV)，请先执行 'make install' 完成依赖安装" ; \
	  exit 1 ; \
	fi
	@echo "[run] 启动 plaso_downloader.main"
	. $(VENV)/bin/activate && $(PYTHON) -m plaso_downloader.main $(RUN_ARGS)

shell: venv
	@echo "[shell] 打开已激活的虚拟环境"
	. $(VENV)/bin/activate

clean:
	@echo "[clean] 删除 $(VENV) 及 Python 缓存"
	rm -rf $(VENV)
	find src -name '__pycache__' -type d -prune -exec rm -rf {} +
