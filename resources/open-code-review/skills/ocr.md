# OpenCodeReview skill（占位）

当用户说"审查一下 <commit/diff/路径>"时，调用 open-code-review CLI：

```
open-code-review --target <branch-or-path> --format markdown
```

输出：审查结果（markdown 报告），保存到 /work/review-<timestamp>.md。
