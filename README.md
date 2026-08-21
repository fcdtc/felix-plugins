# felix-plugins

felix 的个人 Claude Code 插件市场（marketplace）。

## 结构

```
.
├── .claude-plugin/marketplace.json      # 市场定义（市场名：felix）
├── plugins/
│   └── felix-skills/                    # 个人技能包插件
│       ├── .claude-plugin/plugin.json
│       └── skills/<skill-name>/SKILL.md # 各个 skill 放这里
└── README.md
```

## 使用

```text
# 添加本市场（本地路径）
/plugin marketplace add fcdtc/felix-plugins

# 安装技能包
/plugin install felix-skills@felix
```

安装后 skill 以 `felix-skills:<skill名>` 形式出现。
