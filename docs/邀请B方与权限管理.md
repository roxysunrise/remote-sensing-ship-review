# 邀请 B 方与权限管理

日期：2026-10-27。仓库：<https://github.com/roxysunrise/remote-sensing-ship-review>。

当前仅创建仓库和邀请说明，未向任何人发送邀请。B 方用户名尚未提供。

## 1. 邀请日常共同开发（当前推荐）

1. 向 B 方取得其 GitHub 用户名，让其使用自己的账号。
2. A 打开[仓库访问设置](https://github.com/roxysunrise/remote-sensing-ship-review/settings/access)。如要求重新认证，使用自己的 GitHub 登录方式。
3. 在 Settings → Collaborators（部分界面显示 Collaborators and teams / Manage access）中点击 Add people，搜索并核对 B 的用户名，发送邀请。
4. B 在 GitHub 通知或邀请邮件中接受邀请；私有仓库只有链接而未获授权时不能访问。
5. A 回到访问设置确认 B 已接受，而非仍处于 Pending；B 确认能打开仓库并推送自己的功能分支。
6. B 阅读管理协议，在确认 Issue 中接受或提出修改，再通过一个小型 PR 验证协作流程。

个人账号仓库官方文档区分 Owner 与 Collaborator。协作者可参与代码、Issue 和 PR 等日常协作；邀请成功不能直接认定获得修改仓库设置、管理成员或删除仓库的管理员权限。以实际设置页面和有效权限为准，不共享 A 的账号绕过权限限制。

克隆命令：

```powershell
gh repo clone roxysunrise/remote-sensing-ship-review
```

这是私有仓库，B 需先接受邀请，并使用自己的 GitHub 身份登录 Git/gh。A 的本地现有目录已经绑定该仓库，无需再次克隆覆盖。

## 2. 如果确实需要双方管理仓库设置

建议使用 GitHub Organization 承载仓库，在组织仓库中明确分配 Write、Maintain 或 Admin 等角色，而不是将协作者身份当成共同 Owner。

1. 双方先确认是否需要管理成员、规则和仓库设置；只开发、合并代码通常不需要 Admin。
2. A 创建或选定组织，再邀请 B；组织名称、费用和所有权由双方确认。
3. 将本仓库转移到组织前，确认目标权限、计划功能、自动化、Secrets、环境和远端地址的影响；执行一次备份并记录迁移。
4. 在组织仓库 Settings → Collaborators and teams 中赋予适当角色。日常代码协作使用 Write；维护管理按能力选择 Maintain；需要仓库敏感设置权限时使用 Admin，并检查组织政策限制。
5. 组织 Owner 的权限大于单仓库 Admin。只有确需共同管理组织时才讨论共同 Owner，不能将两者混为一谈。

本次没有创建组织、转移仓库或授予他人 Admin。

## 3. 分支保护

建议 main 要求 PR，公共接口变更要求对方审阅，必要自动检查通过后合入。GitHub 是否能对当前私有仓库强制执行，取决于账号计划和仓库规则支持情况。实际配置后应在这里记录结果；没有配置时按协议人工执行，不声称已开启保护。

## 4. 官方参考

- [邀请个人仓库协作者](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)
- [个人仓库权限](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)
- [组织仓库角色](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)
- [受保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
