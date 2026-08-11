# 在另一台电脑使用

## Codex（推荐）

把整个 `book-companion` 文件夹复制到 Codex Skills 目录：

- macOS/Linux：`~/.codex/skills/book-companion/`
- Windows：`%USERPROFILE%\.codex\skills\book-companion\`

重启 Codex。新建一个空文件夹作为书籍项目并在 Codex 中打开，放入书籍，发送：

```text
使用 $book-companion 处理这本书：<书籍路径>。
请从 extract → inventory 开始，严格使用低 Token 工作流，持续处理到全部章节验证并合并。
```

中断后在同一项目发送：

```text
继续使用 $book-companion，只处理 manifest 中未完成或失败的单元，不要重做 completed 单元。
```

## ChatGPT Project

1. 把 `assets/AGENTS.md` 全文粘贴到 Project Instructions。
2. 上传书籍和 `manifest.template.json`。
3. 发送启动指令：先 extract 提取文本、inventory 清点结构，然后按单元分批处理。
4. 无法写本地目录时，要求每批返回可下载文件。

## 迁移现有项目

复制整个项目文件夹，至少保留：`AGENTS.md`、`manifest.json`、`source_text/`、`reader/` 与 `evidence/`。
