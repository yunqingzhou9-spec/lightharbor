# 拾光 · LightHarbor

让底片，回到各自的位置。本地照片与延时摄影整理工具，复制保留原件，照片不上传。

当前版本：**Mac 1.5.0** · **Windows 1.5.0 x64 便携预览版**。

- [下载 Mac 1.5.0](https://github.com/yunqingzhou9-spec/lightharbor/releases/tag/v1.5.0)：macOS 13 及以上，Apple 芯片 / Intel 通用二进制。
- [下载 Windows 1.5.0 x64 预览版](https://github.com/yunqingzhou9-spec/lightharbor/releases/tag/v1.5.0-windows-preview)：Windows 10/11 Intel / AMD 64 位。完整解压后运行 `Start-LightHarbor.cmd`，需要 WebView2 Runtime 与 .NET Framework 4.8，无需另装 Python。
- [版本与验证范围](RELEASE.md) · [反馈问题](https://github.com/yunqingzhou9-spec/lightharbor/issues)

## 1.5.0 整理优化

- 多个来源文件夹统一整理复制；父子目录只扫描一次，同名照片按实际来源隔离配对。Mac 使用系统多选窗口，Windows 保留自定义目录缩略图窗口并支持多选。
- 视频独立归档；JPG / RAW 可分开存放。当天只有一种拍摄类型时省略类型目录，已有归档结构保持不变。
- 完成后显示复制结果并可继续分批选择；已复制照片退出当前选择。
- 同一任务分批复制复用目录及延时序列，按内容核对已有副本，明确选择跳过或再复制；不覆盖文件。
- 两端共用1.5.0产品版本，平台验证状态分别记录。

## 照片工作台

按拍摄日期或拍摄组浏览与选择，识别拍摄间隔规律的延时序列；RAW / JPEG 与可靠关联的 XMP、AAE 一起处理。筛选与搜索用于浏览，不改变已选复制范围。类型修正与勾选独立，不改动原件。支持缩略图、大图、信息查看、复制格式、暂停继续、失败重试、本机整理记录与任务恢复。

## 使用

1. 添加一个或多个来源，再选择独立保存文件夹。保存位置不能与任何来源相同或互相包含。
2. 扫描、浏览并选择复制范围，检查类型、格式和归档设置。增删来源后需重新扫描，未变化照片保留选择和类型修正。
3. 点击“复制所选”，核对去向；发现已有副本时明确选择跳过匹配文件或全部再复制一份，然后确认。
4. 完成后可继续选择下一批。恢复任务需要重新核对并确认。

Mac：解压 ZIP，将 `拾光.app` 放到本机 Applications，双击启动。已包含运行时。建议使用非云同步的应用目录。

Windows：完整解压 ZIP，运行 `Start-LightHarbor.cmd`；不要直接在压缩包内启动。自定义目录窗口显示照片缩略图与文件名，保留目录浏览、多来源选择和取消行为。照片仅供预览，不会因选择目录而被修改。

## 清理照片

清理仅支持单一来源，不随多来源复制扩大删除范围。默认直接清理，也可选择“核对备份后清理”，该模式要求另一磁盘卷上的完整一致备份。最终确认会**永久删除所选照片及可靠关联文件，不经过废纸篓**；视频和未关联文件不纳入照片清理。日志保存在本机任务目录旁的 cleanup-logs。

## 验证范围与平台差异

Mac 在 Apple 芯片本机完成包与启动验证，构建和签名核对包含 arm64 / x86_64；未宣称 Intel 实机验证。Mac 使用临时签名，**未做 Developer ID 签名或 Apple 公证**。

Windows 仍是**预览版，尚未在 Windows 实机运行验证**。已检查固定依赖、x64包内容、共享整理逻辑和目录窗口逻辑；这不等于验证 WebView2/.NET、Windows路径权限、驱动器和原生窗口集成。Windows 包不是已签名安装器。

Windows 不支持 Apple Vision 景物识别。RAW低清优先内嵌预览，高清按可用内嵌或 LibRaw 解码处理，最长边3200且不放大小图；HEIC/HEIF 使用随包解码库。机型和格式支持有差异，预览失败不代表原件不能复制。预览在本机完成，不修改原件。请保留系统安全保护，首次使用先用临时素材验证。

延时识别默认至少30次拍摄、20%间隔容差。RAW + JPEG 算一次。拍摄时间不可靠时提示并使用修改日期；识别结果需要人工检查。地点为 GPS 坐标分区，不是城市名称。

## 本机记录与隐私

仅监听127.0.0.1，操作需要随机令牌及当前计划版本。没有远程脚本、照片上传或收费 AI 接口。反馈时请遮挡个人路径、照片和 GPS，不分享含令牌的启动地址。

Mac任务：`~/Library/Application Support/LightHarbor/tasks`；Windows任务：`%LOCALAPPDATA%\LightHarbor\tasks`。普通复制保留原件，正常停止保留完整副本并清理未完成副本。断电或强制结束后请核对结果。

## 源码与构建

源码为发布时明确列出的程序、测试与说明文件，不包含个人照片、本机任务或私有开发记录。源码运行需要 Python3.10+；Mac 构建要求见[构建说明](macos/BUILD.md)，Windows 固定依赖见[清单](windows/dependencies.json)和[便携包说明](windows/先读我.txt)。第三方许可证随包提供。

```sh
xcrun swiftc -O MediaProbe.swift -o media-probe
python3 app.py
python3 -m unittest discover -v
```

下载后可用 Release 附件中的 SHA-256 校验文件核对安装包。历史版本继续保留。
