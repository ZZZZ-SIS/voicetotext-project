# 提交审核步骤

1. 解压本压缩包。
2. 在 GitHub 新建仓库，例如 `voice-to-text-review`。
3. 上传本目录全部文件，不要上传本地虚拟环境目录。
4. 提交代码到 GitHub：

```powershell
git init
git branch -M main
git add .
git commit -m "Initial security review package"
git remote add origin https://github.com/<你的用户名>/voice-to-text-review.git
git push -u origin main
```

5. 加入 `secure-artifacts` 组织后，将仓库 Fork 到 `secure-artifacts` 组织下。
6. 在本地创建发布标签：

```powershell
git tag v1.0.0
git push origin v1.0.0
```

7. 到 GitHub Actions 页面确认构建成功。
8. 到审核平台提交项目地址，例如：

```text
https://github.com/secure-artifacts/voice-to-text-review
```
