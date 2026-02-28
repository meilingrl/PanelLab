# 将 PanelLab 关联到 GitHub

## 1. 在 GitHub 上创建仓库

1. 打开 [https://github.com/new](https://github.com/new)
2. **Repository name** 填：`PanelLab`
3. **Description** 可选填：`轻量级服务器运维管理面板（课程实践项目）`
4. 选择 **Public**
5. **不要**勾选 "Add a README file"（本地已有）
6. 点击 **Create repository**

## 2. 关联本地仓库并推送

在项目根目录 `E:\PanelLab` 下执行（把 `你的用户名` 换成你的 GitHub 用户名）：

```bash
git remote add origin https://github.com/你的用户名/PanelLab.git
git push -u origin main
```

若使用 SSH：

```bash
git remote add origin git@github.com:你的用户名/PanelLab.git
git push -u origin main
```

## 3. 设置 Git 用户信息（首次使用建议）

让提交在 GitHub 上显示为你的账号，请执行一次（替换为你的信息）：

```bash
git config --global user.name "你的名字或昵称"
git config --global user.email "你的GitHub邮箱"
```

之后新提交会使用该身份；本仓库已有一笔用本地身份提交的记录，可保留不变。
