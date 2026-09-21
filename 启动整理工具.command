#!/bin/zsh
cd -- "${0:A:h}"
if [[ ! -x ./media-probe ]]; then
  echo '首次运行：正在准备本地图片识别组件…'
  xcrun swiftc -O MediaProbe.swift -o media-probe || echo '图片组件准备失败，仍可使用基础日期和类型整理。'
fi
if command -v python3 >/dev/null 2>&1; then
  python3 app.py
else
  echo '没有找到 Python 3。请安装 Python 3.10 或更新版本后再启动。'
fi
read -k 1 '?按任意键关闭此窗口…'
