# 拾光 · LightHarbor

让底片，回到各自的位置。本地照片与延时摄影整理工具，复制保留原件，照片不上传。

当前发布：**Mac 1.3.13** · **Windows 1.4.2 预览版**。

- [下载 Mac 版](https://github.com/yunqingzhou9-spec/lightharbor/releases/tag/v1.3.13)：macOS 13 及以上，Apple 芯片 / Intel 通用二进制。
- [下载 Windows x64 预览版](https://github.com/yunqingzhou9-spec/lightharbor/releases/tag/v1.4.2-windows-preview)：Windows 10/11 Intel / AMD 64 位。完整解压后运行 `Start-LightHarbor.cmd`，需要 WebView2 Runtime 与 .NET Framework 4.8，无需另装 Python。
- [版本与验证范围](RELEASE.md) · [反馈问题](https://github.com/yunqingzhou9-spec/lightharbor/issues)

## 照片工作台

轻侧栏、大照片和集中工具栏；按日期或拍摄组浏览，信息与调整共用一个入口。整理记录只显示历史和恢复工具。

- 按日期整理照片，识别拍摄间隔规律的延时序列；RAW / JPEG 与可靠关联的 XMP、AAE 一起处理。
- 日期和照片复选框控制复制范围。搜索与筛选用于查找，不代表排除。
- 拍摄组改名、合并、拆分、照片移组与撤销；目录和格式分层、照片预览及抽样保留。
- 默认按文件名快速查重，可选完整内容校验。发现匹配文件后，必须手动选择跳过或再复制一份；不会默认跳过或覆盖已有文件。
- 显示传输进度、暂停与继续、失败重试、全量执行明细和本机整理记录。恢复任务后重新核对并确认。

## 使用

1. 选择来源和独立的保存文件夹，两者不能相同或互相包含。
2. 扫描后浏览照片、选择复制范围，按需调整分组和归档设置。
3. 点击“复制所选”，检查计划与重复处理方式，再确认复制。

Mac 安装：解压 ZIP，把拾光.app 放到 Applications 或自己的工具目录，双击启动。已含运行时。

Mac 当前为本地临时签名，未做 Apple Developer ID 签名和公证。请保留系统安全保护，确认来源后按系统提示处理首次打开。通用架构为构建目标，不代表在所有设备和系统上实测。

## 清理照片

可以选择任意照片文件夹。默认直接清理，不强制检查备份；扫描后照片直接可见，选择后仅一次最终删除确认。取消确认保留选择，完成后可继续选择剩余照片。也可在设置中选择“核对备份后清理”，该模式要求另一个磁盘卷上的完整一致备份。

最终确认会**永久删除所选照片及可靠关联文件，不经过废纸篓**，请自行确认可以删除。视频和未关联文件不纳入照片清理。日志保存在本机任务目录旁的 cleanup-logs 中。

## 功能边界与平台差异

延时识别是辅助判断，默认至少 30 次拍摄、20% 间隔容差。RAW + JPEG 算一次；可靠拍摄时间不足时使用修改日期归档并提示，不能保证检测出延时。地点是 GPS 坐标分区，不是城市名称。格式预览取决于系统和相机，预览失败不代表原件不能复制。

Windows 不支持 Apple Vision 景物识别；RAW、HEIC/HEIF 缩略图支持有限。本版保持预览状态：已验证共享逻辑、界面和包内容，**尚未在 Windows 实机完成独立窗口、文件对话框和系统集成端到端验证**。

## 本机记录与隐私

仅监听 127.0.0.1，操作需要随机令牌和当前计划版本。没有远程脚本、照片上传或收费 AI 接口。请勿分享包含令牌的启动地址，反馈问题时遮挡照片、个人路径和 GPS。

Mac 任务位于 `~/Library/Application Support/LightHarbor/tasks`，Windows 位于 `%LOCALAPPDATA%\LightHarbor\tasks`。历史记录保存在本机，归档目录的 `.整理记录` 原始清单也保留。普通复制不修改来源；正常停止会清理未完成副本、保留完整副本。断电或强制结束后请核对结果。

## 源码与构建

Mac 源码运行需要 Python 3.10+ 和 Xcode Command Line Tools。完整应用见 [macOS 构建说明](macos/BUILD.md)。Windows 便携包由官方嵌入式 Python 与发行依赖组装，见 [使用说明](windows/先读我.txt) 和 `windows/build_portable.py`。

```sh
xcrun swiftc -O MediaProbe.swift -o media-probe
python3 app.py
python3 -m unittest discover -v
```

第三方许可证随运行时附带。发布只含明确列出的程序和说明文件，不包含个人照片、本机计划或私有开发记录。
