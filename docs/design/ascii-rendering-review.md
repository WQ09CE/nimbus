# ASCII Architecture Diagram Rendering Review Request

## Current Implementation Status

We are attempting to render ASCII architecture diagrams (Box Drawing characters) in the Web UI.
Users report that the result "looks uncomfortable" and lines are not straight, especially when mixed with CJK characters.

### 1. Detection Logic (`MarkdownRenderer.tsx`)
We detect diagrams using a regex for Box Drawing Unicode block:
```typescript
const isDiagram = /[\u2500-\u257F]/.test(codeContent);
```
If detected, we apply the `.is-diagram` CSS class.

### 2. CSS Styling (`globals.css`)
We apply specific styles to `.is-diagram .code-block`:

```css
.is-diagram .code-block {
  line-height: 1.15 !important; /* Tightened to connect vertical lines */
  font-family: "Menlo", "Consolas", "Courier New", monospace !important; /* Classic monos */
  white-space: pre !important; /* Prevent wrapping */
}
```

### 3. The Core Problem: CJK Alignment

The diagram provided by the user contains mixed English and Chinese:

```text
│  │                     Process Pool                       │   │
...
│  │                     进程池                             │   │
```

**Issue**: In standard web rendering, Monospace fonts do NOT guarantee that CJK characters are exactly 2x the width of ASCII characters.
*   ASCII `a` width: 1ch
*   CJK `中` width: ~1.6ch - 2.0ch (depending on font)

If the font renders CJK as < 2.0ch, the row with Chinese characters will be **shorter** than the row with English, causing the right border `│` to be misaligned to the left.

### 4. Comparison with Terminal (TUI)
Terminals (like iTerm2, Windows Terminal) force CJK characters to grid cells (Ambiguous Width = Wide usually).
Browsers do not enforce a character grid.

### 5. Proposed Solutions for Expert Review

**Option A: Web Font (Sarasa Gothic / Inconsolata)**
Load a web font known for strict 2:1 CJK alignment.
*   *Pros*: Perfect alignment.
*   *Cons*: Performance hit (large font files), CJK web fonts are huge.

**Option B: CSS `font-variant-east-asian`?**
Does `font-variant-east-asian: full-width` help?
It forces full width forms, but usually for punctuation.

**Option C: SVG / Mermaid Conversion (Radical)**
Instead of trying to render ASCII art as text, detect it and convert it to:
1.  **SVG**: Render text to SVG where we can control positioning?
2.  **Mermaid**: Ask the LLM to output Mermaid syntax instead of ASCII art.
    *   *Pros*: Perfect rendering, responsive.
    *   *Cons*: Requires prompt engineering change.

**Option D: "Close Enough" CSS Hacks**
*   Use `SimHei` or `NSimSun` (Windows) / `PingFang SC` in the stack?
*   `font-family: "Sarasa Mono SC", "Consolas", "Microsoft YaHei", monospace`?

We request advice on the best path forward to make these diagrams look "professional" on the Web UI.
