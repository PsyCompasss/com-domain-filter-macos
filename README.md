# COM域名筛选器

这是一个只面向 macOS 的 Python 桌面软件。它按照选中的字符、规律、固定开头和固定结尾随机生成 `.com` 域名，在 Cloudflare Registrar 页面逐个查询，只把“名称完全一致且可注册”的结果写入一个 Excel 文件。

## 下载

- [前往 Releases 下载最新版 Mac 软件](https://github.com/PsyCompasss/com-domain-filter-macos/releases/latest)
- 当前打包版本为 Apple 芯片 Mac 版；源码可按下文步骤自行运行或构建。

## 使用方法

1. 打开“COM域名筛选器”。
2. 在“生成规则”中勾选允许使用的字母和数字。
3. 勾选一个或多个规律；`A、B、C、D` 是不同字符的占位符。
4. 按需填写固定开头和固定结尾。例如开头 `musa`、规律 `AAA`、结尾 `88`。
5. 在“运行设置”中输入查询间隔（秒）、停止条件和 Excel 保存路径。
6. 点击窗口上方、分页按钮之上的“开始查询”。软件会在 Cloudflare 搜索框中只输入名称部分，不输入 `.com`，然后按完整 `.com` 结果进行精确比对。

## 验证与后台运行

- 浏览器平时会尝试最小化，在后台进行查询。
- Cloudflare 可能随时要求安全验证或暂时拒绝自动查询。
- 软件检测到验证、超时或 Cloudflare 错误页时会暂停并弹窗。
- 请在弹出的浏览器中完成验证；如果显示错误页，请手动刷新。搜索页面恢复后，软件会继续。
- 软件不会自动破解或绕过验证码。

## 保存内容

- Excel 只记录完全一致且可注册的 `.com` 域名。
- 软件内部会记录已经测试过的域名，用于去重和断点续查。
- 内部状态保存在 `~/Library/Application Support/COM域名筛选器/`。
- 更换 Excel 路径时，软件会把内部已经找到的可注册结果同步到新文件。

## 注意事项

- 第一版只适配 `https://domains.cloudflare.com/`。
- Cloudflare页面结构、查询接口或反自动化策略发生变化时，软件可能需要更新。
- 查询间隔过短更容易触发验证或临时限制。
- 软件只检查可注册状态，不会购买、注册或提交付款。

## 源码运行

需要 Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH=playwright-browsers .venv/bin/playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=playwright-browsers .venv/bin/python app.py
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```
