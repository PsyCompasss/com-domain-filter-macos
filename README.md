<div align="center">

<img src="assets/app-icon-b-v2.png" width="132" alt="COM域名筛选器图标">

# COM域名筛选器

### 用你选定的字符和规律自动组合域名，在真实注册商页面筛选可注册的 `.com`

<p>
  <img src="https://img.shields.io/badge/版本-v1.5.2-1769ff?style=flat-square" alt="版本 v1.5.2">
  <img src="https://img.shields.io/badge/平台-macOS-111111?style=flat-square&logo=apple" alt="macOS">
  <img src="https://img.shields.io/badge/芯片-Apple%20Silicon-555555?style=flat-square" alt="Apple Silicon">
  <img src="https://img.shields.io/badge/Python-3.12-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/后缀-.com-12a150?style=flat-square" alt="只查询 .com">
</p>

<p>
  <a href="https://github.com/PsyCompasss/com-domain-filter-macos/releases/latest"><strong>下载最新版</strong></a>
  ·
  <a href="#快速开始">快速开始</a>
  ·
  <a href="#使用说明">使用说明</a>
  ·
  <a href="#源码运行">源码运行</a>
</p>

</div>

---

## 软件简介

COM域名筛选器是一款 macOS 桌面软件。你可以自由选择字母、数字和连字符，再用“固定文字、常用规律、自定义规律、不限随机”组合块搭建域名结构。软件会在 Cloudflare Registrar 或阿里云万网页面逐个查询，只保存名称完全一致且可注册的 `.com` 域名。

它适合需要长时间批量测试域名、又不想守在电脑前反复输入和整理结果的用户。

## 核心功能

| | 功能 | 说明 |
|:--:|---|---|
| 🧩 | **自由组合域名结构** | 固定文字、常用规律、自定义规律和不限随机可以任意添加、排序、复制和删除 |
| 🔤 | **可选字符池** | 自由勾选 26 个英文字母、10 个数字和半角连字符 `-` |
| 🧬 | **灵活的规律关系** | 不同规律块可以独立随机，也可以让其中的 `A、B、C` 分别代表同一个字符 |
| 🎯 | **只保留精确结果** | 只记录名称完全一致且页面明确显示可注册的 `.com` 域名 |
| 🌐 | **支持两个查询网站** | 已适配 Cloudflare Registrar 和阿里云万网 |
| 🔄 | **自动恢复查询** | 页面临时打不开、空白或加载超时时，会按设定间隔自动刷新并继续 |
| 🛑 | **三种停止条件** | 可按检测数量、找到数量或手动停止控制任务 |
| 📊 | **统一写入 Excel** | 所有可注册结果追加到同一个 Excel 文件，并自动去重 |
| 🧠 | **查询历史管理** | 可以查看、搜索、导出或删除已查询记录；删除后可重新查询相应域名 |
| ⏯️ | **暂停后修改规则** | 暂停期间修改组合规则，继续时从下一条域名开始采用新规则 |
| 🖱️ | **适合后台运行** | 软件不会自动最小化、移动或关闭 Chrome；用户可以正常处理其他工作 |

## 界面预览

### 生成规则

自由搭建域名结构，实时查看组合结果与域名长度。

<p align="center">
  <img src="docs/images/ui-rules-v1.4.0.png" width="100%" alt="生成规则界面">
</p>

<table>
  <tr>
    <td width="50%" align="center"><strong>运行设置</strong></td>
    <td width="50%" align="center"><strong>可注册结果</strong></td>
  </tr>
  <tr>
    <td><img src="docs/images/ui-run-v1.4.0.png" alt="运行设置界面"></td>
    <td><img src="docs/images/ui-results-v1.4.0.png" alt="可注册结果界面"></td>
  </tr>
  <tr>
    <td>连接查询网站、设置速度、自动刷新、停止条件和 Excel 路径。</td>
    <td>实时查看已经确认可注册的域名，并直接打开结果文件。</td>
  </tr>
</table>

## 组合方式

域名由多个组合块从左到右拼接，每一个位置都可以自由选择类型。

```text
固定文字 + 常用规律 + 不限随机 + 自定义规律 + 固定文字
   abc   +    ABC   +    4位    +    ABCBA   +     88
```

- **固定文字**：直接保留你输入的内容，例如 `abc`、`88`。
- **常用规律**：快速选择 `AAA`、`AABB`、`ABCABC` 等预设结构。
- **自定义规律**：输入任意占位结构，例如 `ABCDDDD`、`ABCBA`。
- **不限随机**：从字符池中随机生成指定长度的内容。
- **独立随机**：每个规律块分别抽取自己的字符。
- **共用字符**：所有规律块中的 `A` 共用一个字符，`B`、`C` 依次类推。

> 规律字母是“占位符”，不是固定输出内容。同一个规律中的相同字母代表相同字符，不同字母代表不同字符。

## 快速开始

### 1. 下载

[前往 Releases 下载最新版 Mac 软件](https://github.com/PsyCompasss/com-domain-filter-macos/releases/latest)

当前提供的是 **Apple Silicon Mac** 版本。

### 2. 打开软件

解压 ZIP 文件后打开 `COM域名筛选器.app`。如果 macOS 第一次打开时进行安全确认，可以在 Finder 中右键点击软件，再选择“打开”。

### 3. 连接 Chrome

在“运行设置”页面选择查询网站，点击“打开/连接 Chrome”。顶部显示“准备就绪”以后，再开始查询。

## 使用说明

1. 在“生成规则”中勾选允许使用的字符。
2. 添加所需的组合块，并调整它们的先后顺序。
3. 选择规律块之间采用“独立随机”还是“共用字符”。
4. 在“运行设置”中选择 Cloudflare 或阿里云万网。
5. 设置查询间隔、页面异常自动刷新间隔、停止条件和 Excel 保存路径。
6. 点击“打开/连接 Chrome”，等待状态变为“准备就绪”。
7. 点击“开始查询”。软件只会把名称部分输入搜索框，不会把 `.com` 一起输入。

## Chrome 与后台运行

- 软件使用 Mac 已安装的 Google Chrome，不会操作你平时打开的标签页。
- 只有“打开/连接 Chrome”按钮可以创建软件专用的 Chrome 窗口。
- “开始查询”只连接已经准备好的窗口，不会另开 Chrome。
- 软件不会自动最小化、置前、移动或关闭 Chrome。
- 你可以手动最小化 Chrome，查询仍会继续。
- 点击“停止”只结束当前查询，不会关闭 Chrome。
- 修改规则后，可以重新连接原来的专用窗口并再次开始。

## 自动刷新与真人验证

- 页面空白、结果加载超时或临时网络故障时，软件会按设定间隔自动刷新。
- 同一个域名连续失败 3 次后会自动跳过，避免任务一直卡住。
- 只有网站明确要求真人验证时，软件才会暂停并提醒。
- 验证码需要在 Chrome 中由用户本人完成，软件不会破解或绕过验证。
- 如果连续两次遇到验证，软件会停止当前任务，避免反复打扰。

查询速度过快更容易触发验证或临时限制，建议根据网站实际情况设置间隔。

## 结果与数据

- Excel 只记录名称完全一致且可注册的 `.com` 域名。
- 软件内部会保存已经测试过的域名，用于去重和断点续查。
- “已查询记录”页面可以搜索、筛选、导出或删除历史；删除记录后，对应域名可以重新查询。
- 更换 Excel 路径时，已经找到的可注册结果会同步到新文件。
- 内部状态和错误日志保存在：

```text
~/Library/Application Support/COM域名筛选器/
```

## 支持的网站

| 网站 | 地址 | 状态 |
|---|---|:--:|
| Cloudflare Registrar | `https://domains.cloudflare.com/` | 已适配 |
| 阿里云万网 | `https://wanwang.aliyun.com/domain` | 已适配 |

软件可以保存其他网站的名称和地址，但必须编写对应的页面适配器后才能查询。

## 使用边界

- 软件只查询域名状态，不会购买、注册域名或提交付款。
- 查询网站的页面结构、接口或反自动化策略发生变化时，软件可能需要更新。
- 网站显示的结果可能随时间变化，准备注册前请在注册商页面再次确认。
- 当前安装包只适配 Apple Silicon Mac。

## 源码运行

需要 Python 3.12 和已安装的 Google Chrome：

```bash
git clone https://github.com/PsyCompasss/com-domain-filter-macos.git
cd com-domain-filter-macos
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 构建 Mac 软件

安装依赖后双击：

```text
重新构建Mac软件.command
```

构建脚本会生成 `.app` 和 ZIP 安装包。正式发布前仍需根据发布方式完成签名、公证和实际 Mac 环境测试。

---

<div align="center">

只查询 `.com` · 只保存完全一致且可注册的结果

</div>
