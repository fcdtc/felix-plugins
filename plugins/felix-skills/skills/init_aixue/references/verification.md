# 初始化验收

## 技能维护时

在插件仓库运行：

```sh
python3 scripts/validate-skills.py
python3 -I -m unittest discover -s plugins/felix-skills/skills/init_aixue/tests -v
python3 -I -m unittest discover -s plugins/felix-skills/skills/init_aixue/assets/centralized/tests -v
```

初始化测试须覆盖预览无写入、重复幂等、自定义文件和符号链接保留、非法配置及仓外路径拒绝、两模式与分支适配、父路径冲突预检和已跟踪运行时绑定拒绝。workspace 测试使用临时 Git 仓库，覆盖全部命令、绑定／身份／项目集／分支冲突、部分恢复、删除备份及本地分支保留。

## 目标仓初始化后

1. 全部文档相对链接、标题锚点和脚本入口存在。集中式补充文档生成到同一个 `docs/development/`，无需技能安装目录。
2. 开发分支、模式及文档归属一致；来源项目名、业务路由、私有 ID 或本机路径均已剥离。
3. 人工交接手册有 RPC Version、Workflow snapshot、Skill、Tool、Host 草稿、身份验收及登录／审批七类场景，各有准备、消息与回读标准。
4. 既有 tracker／领域约定和自定义配置保留，所有 `preserved` 文件完成语义核对；重复调用没有重复章节。
5. 集中式配置只含明确项目，wrapper 可执行，绑定忽略规则生效；不创建运行时绑定。运行目标的 `python3 -m unittest discover -s tests -p 'test_workspace.py' -v`，它仅使用临时仓。
6. 查看实际 Git diff 与状态，确认未覆盖用户工作，未提交／推送，未操作真实工作区或平台。失败、跳过和未验证范围如实报告。

测试通过只证明初始化文件和脚本行为；SCM／BITS／MIA 的真实平台绑定仍按项目实施流程验收。
