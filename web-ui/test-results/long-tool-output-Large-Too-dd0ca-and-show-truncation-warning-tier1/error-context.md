# Page snapshot

```yaml
- dialog "Unhandled Runtime Error" [ref=e4]:
  - generic [ref=e5]:
    - generic [ref=e6]:
      - generic [ref=e7]:
        - navigation [ref=e8]:
          - button "previous" [disabled] [ref=e9]:
            - img "previous" [ref=e10]
          - button "next" [disabled] [ref=e12]:
            - img "next" [ref=e13]
          - generic [ref=e15]: 1 of 1 error
          - generic [ref=e16]:
            - text: Next.js (14.2.35) is outdated
            - link "(learn more)" [ref=e18] [cursor=pointer]:
              - /url: https://nextjs.org/docs/messages/version-staleness
        - button "Close" [ref=e19] [cursor=pointer]:
          - img [ref=e21]
      - heading "Unhandled Runtime Error" [level=1] [ref=e24]
      - paragraph [ref=e25]: "TypeError: Cannot read properties of undefined (reading 'slice')"
    - generic [ref=e26]:
      - heading "Source" [level=2] [ref=e27]
      - generic [ref=e28]:
        - link "src/app/page.tsx (87:51) @ slice" [ref=e30] [cursor=pointer]:
          - generic [ref=e31]: src/app/page.tsx (87:51) @ slice
          - img [ref=e32]
        - generic [ref=e36]: "85 | > 86 | <span className=\"truncate max-w-[150px]\"> > 87 | {session.name || session.id.slice(0, 8)} | ^ 88 | </span> 89 | <span className=\"opacity-0 group-hover:opacity-100 transition-opacity\">▼</span> 90 | </button>"
      - heading "Call Stack" [level=2] [ref=e37]
      - button "Show collapsed frames" [ref=e38] [cursor=pointer]
```