# Doc-Search skill（纯 skill 能力，最简示例）

当用户说"搜一下 <关键词> 相关文档"时，用内置 grep/find 工具搜 /work 下的文件：

```
grep -rn "<keyword>" /work
find /work -name "*<pattern>*"
```

把结果汇总输出到 /work/search-<keyword>.md。
