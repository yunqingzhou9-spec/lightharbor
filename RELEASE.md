# Mac 1.5.0 / Windows 1.5.0 x64 便携预览版

本次将两端产品版本统一至1.5.0，包含七项整理优化：视频独立归档、JPG/RAW分开放置开关、单一类型省略类型目录、统一版本、完成提示及继续分批选择、分批目录复用及重复处理、多来源文件夹统一整理复制。

Mac保持系统目录窗口并支持多选；Windows保留自定义目录缩略图选择窗口及多选来源。清理仍限单来源。新增或删除来源后重扫；父子目录去重、来源隔离配对，保留未变化照片的选择与类型修正。已有归档和延时目录保持稳定，不搬动历史内容。

## 下载与运行

- Mac：`LightHarbor-1.5.0-macOS-universal.zip`，macOS13+，arm64/x86_64通用；解压后放入本机Applications。
- Windows：`LightHarbor-1.5.0-preview-Windows-x64.zip`，Windows10/11 x64；完整解压运行`Start-LightHarbor.cmd`。需要Microsoft Edge WebView2 Runtime与.NET Framework4.8，无需另装Python。
- 两端分别附SHA-256及来源清单，Windows包内提供依赖清单、文件哈希和第三方许可证/供应商源码。

## 验证与限制

Mac包沿用已验收1.5.0候选；共享逻辑208项回归、独立行为验证及审查已通过，Apple芯片本机启动和生成素材的复制/恢复、RAW高清预览、方向等已验证；通用架构及临时签名已核验。未声称Intel实机测试或全机型格式兼容。

Windows为预览包，**未在Windows实机运行验证**。使用固定哈希的官方Python3.14.7 x64嵌入式运行时和发行依赖；包与源码一致性、共享逻辑、目录缩略图选择及媒体读取逻辑检查不等于Windows原生验证。WebView2/.NET、窗口生命周期、Windows路径权限、磁盘卷及不同相机格式仍需实机试用。RAW使用rawpy/LibRaw，HEIC/HEIF使用pi-heif；Apple Vision景物识别不可用。

Mac未做Developer ID签名或Apple公证；Windows不是已签名安装器。保留系统安全保护，不提供绕过步骤。

测试仅使用生成临时素材，发布不包含真实照片、个人路径、本机任务、设置或私有开发记录。普通复制保留原件；清理最终确认会永久删除所选及关联文件，不经过废纸篓，请先核对。

此文件描述1.5.0候选交付范围；实际Release附件、校验和及Git标签为发布结果依据。历史发布保留。
