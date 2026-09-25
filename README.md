# 杭州教师编求职雷达（简化上传版）

这个版本专门用于 GitHub 网页手动上传：所有可见文件都放在仓库根目录。

需要上传到仓库根目录的文件：
- index.html
- jobs.json
- meta.json
- sources.json
- update_jobs.py
- requirements.txt

随后在 GitHub 网页中手动创建：
`.github/workflows/update.yml`

把 `WORKFLOW_TO_PASTE.txt` 的内容粘贴进去即可。

最后在 Settings -> Pages 中开启 GitHub Pages。
