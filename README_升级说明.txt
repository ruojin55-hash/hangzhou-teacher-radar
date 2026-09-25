请在 GitHub 仓库根目录覆盖上传以下 5 个文件：
- index.html
- jobs.json
- meta.json
- sources.json
- update_jobs.py

不需要修改 .github/workflows/update.yml，因为活动数据和岗位数据统一保存在 jobs.json 中，现有工作流仍会自动提交 jobs.json 和 meta.json。

上传后：
1. Commit changes
2. Actions -> Update teacher jobs -> Run workflow
3. 等绿色 Success
4. 刷新 GitHub Pages 网站

新功能：
- 独立“宣讲会 / 双选会”页签
- 监控浙江大学、杭州师范大学、华东师范大学、北京师范大学、浙江师范大学、华中师范大学等官方就业平台
- 活动类型、未来7天/30天、27届、官方来源筛选
- 继续保留原来的收藏与求职进度 LocalStorage
