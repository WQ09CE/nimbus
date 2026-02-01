# AI Council Report: 你看看这个 docs/design/ascii-rendering-review.md。

2026/2/1 15:52:25

### Opinion from Claude Opus 4.5 (Deep Thinker)

作为一名 AI 顾问，我已经详细审阅了 `docs/design/ascii-rendering-review.md` 文档。该文档描述了 Web UI 在渲染包含中日韩 (CJK) 字符的 ASCII 架构图时遇到的对齐和显示问题。

以下是基于顾问视角的分析和建议：

### 核心问题分析
Web 浏览器中的字体渲染机制与终端（Terminal）完全不同。
*   **终端**：通常强制执行网格布局，CJK 字符被强制占用 2 个单元格宽度（Wide），ASCII 占用 1 个。
*   **浏览器**：使用比例或等宽字体渲染。即使是等宽字体（Monospace），如果不专门针对 CJK 优化，中文字符的宽度往往不是英文字符的严格 2 倍（例如可能是 1.6 倍或 2.05 倍）。这导致包含中文的 ASCII 图表在垂直方向无法对齐，右侧边框会错位。

### 对所提方案的评估

| 方案 | 评价 | 建议 |
| :--- | :--- | :--- |
| **Option A: Web Font (如更纱黑体)** | **效果最好，但在 Web 端不可行**。CJK Web Font 体积过大（通常 5MB-20MB），不仅拖慢加载，还会造成文字闪烁（FOIT）。 | **不推荐**，除非是离线应用。 |
| **Option B: CSS 属性** | `font-variant-east-asian` 对解决字符宽度比例（2:1）帮助有限，它更多用于控制字形变体。 | **无效**，无法根治对齐问题。 |
| **Option C: 转 Mermaid (架构升级)** | **最佳长期方案**。从 ASCII 艺术转向结构化图表语言（Mermaid）。这解决了渲染问题，还支持响应式和交互。 | **强烈推荐**作为长期演进方向。 |
| **Option D: CSS Hack (字体栈)** | **最佳短期缓解方案**。虽然无法做到像素级完美，但可以通过优化 `font-family` 顺序在主流系统上获得“可接受”的效果。 | **推荐**作为当前修复方案。 |

### 顾问建议 (Consultant's Recommendation)

针对当前阶段，建议采取 **“双轨制”策略**：

#### 1. 短期修复：优化 CSS 字体栈 (Option D 的改良版)
不要依赖单一的 `monospace`。针对不同操作系统指定“最安全”的预装字体组合。

建议将 CSS 修改为：

```css
.is-diagram .code-block {
  /* 使用系统预装的、已知的具有较好中英宽比的字体 */
  font-family: "Menlo", "Consolas", "Monaco", "Liberation Mono", "Lucida Console", monospace !important;
  /* 稍微收紧行高以连接垂直线字符 */
  line-height: 1.2 !important; 
  /* 确保不换行 */
  white-space: pre !important; 
  /* 强制字符间距（虽然不能完全解决 CJK 问题，但有助于减少微小误差） */
  font-variant-ligatures: none;
}
```
*注意：在 Windows 上 `Consolas` + `Microsoft YaHei` 的组合通常会有对齐问题。但在不引入 Web Font 的情况下，这是不得不做的妥协。*

#### 2. 长期演进：引入 Mermaid 支持 (Option C)
ASCII 图表在现代 Web 应用中显得过时且难以维护（尤其在移动端）。
*   **Prompt Engineering**: 修改 System Prompt，指示 AI 在需要画图时优先输出 `mermaid` 代码块，而不是 ASCII Art。
*   **Renderer**: 在前端引入 `mermaid-js` 进行渲染。
*   **Fallback**: 仅当 AI 坚持输出 ASCII 时，才回退到文本渲染。

### 总结
当前请优先调整 CSS 字体栈以缓解视觉不适，但不要试图在 Web 上追求完美的 ASCII + CJK 对齐（这是与浏览器渲染机制对抗，投入产出比极低）。真正的解决方案是引导模型输出结构化图表（Mermaid）。


### Opinion from GPT-5.2 (Generalist)

你好。作为 AI 顾问，我已经仔细审阅了 `docs/design/ascii-rendering-review.md`。

这是一个 Web 开发中非常经典且棘手的排版难题：**Web 浏览器中的混合字符（ASCII + CJK）等宽对齐问题**。

以下是我的专业评估和建议：

### 核心问题确认
正如文档分析的，终端（Terminal）是基于**网格（Grid）**强制渲染的（1 个 CJK = 2 个 ASCII 宽），而浏览器是基于**字体度量（Metric）**渲染的。除非字体本身经过严格设计（如 Sarasa Gothic 更纱黑体），否则不同字体族（fallback fonts）之间几乎不可能实现完美的 2:1 对齐。

### 对现有方案的深度点评

1.  **Option A (Web Fonts): 成本过高**
    *   加载数 MB 的中文字体（如更纱黑体）仅为了显示 ASCII 图表，对 Web 性能是巨大的浪费。除非使用字体子集化（Subsetting）技术，否则不推荐。

2.  **Option B (CSS Properties): 无效**
    *   `font-variant-east-asian` 控制的是字形（Glyph form），无法强制改变不同字体之间的宽度比例。

3.  **Option C (Mermaid/SVG): 最优长远方案 (Best Practice)**
    *   ASCII Art 本质上是纯文本时代的产物。在 Web 时代，结构化数据（Mermaid）才是正解。它不仅解决了对齐问题，还带来了交互性和响应式能力。虽然需要调整 Prompt，但这属于技术债务的正确偿还方向。

4.  **Option D (Font Stack Hacks): 仅能缓解，无法根治**
    *   不同操作系统（Windows, macOS, Linux）的预装字体差异巨大。CSS 字体栈只能做到“在开发者的机器上看起来不错”，很难保证用户端的一致性。

### 我的专家建议 (Recommendation)

我建议采取 **"Canvas 网格模拟 (Canvas Grid Simulation)"** 作为中短期的最佳解决方案，同时长期向 **Mermaid** 迁移。

#### 为什么选择 Canvas/SVG 模拟？
与其试图强迫浏览器按排版文本的方式对齐（这很难），不如**模拟终端的渲染方式**。

**具体方案：**
编写一个轻量级的 React 组件（`<TerminalTextRenderer />`），不使用 `<pre>`, 而是使用 `<canvas>` 或 `<svg>`。
1.  **解析文本**：将字符串按行分割。
2.  **网格计算**：设定单格宽度 `W` 和高度 `H`。
3.  **强制绘制**：
    *   遍历每个字符。
    *   如果是 ASCII，在 `x` 处绘制，`x += W`。
    *   如果是 CJK，在 `x` 处绘制，`x += 2*W`（无论该字体的实际宽度是多少，都强制居中画在 2W 的格子里）。

**优点：**
*   **完美对齐**：100% 还原终端效果，线条绝对笔直。
*   **零依赖**：不需要加载庞大的 Web Font。
*   **兼容性**：在所有设备上显示效果一致。

---

#### 立即行动建议 (Quick Fix)

如果你现在没有时间做 Canvas 渲染，想要立刻缓解 CSS 里的问题，建议修改 `globals.css` 中的字体栈，加入一些已知对齐较好的字体，并**强制回退到宋体（SimSun）**作为最后的防线（宋体在 Windows 下是严格 2:1 的，虽然不够美观，但能保证对齐）。

你可以尝试将 CSS 修改为：

```css
.is-diagram .code-block {
  line-height: 1.2 !important;
  /* 优先尝试 Sarasa (如果有), 然后是 macOS 的 Menlo, Windows 的 Consolas */
  /* 最后使用 "SimSun" (宋体) 或 "NSimSun" (新宋体) 强制对齐 CJK */
  font-family: "Sarasa Mono SC", "Menlo", "Consolas", "Monaco", "Courier New", "SimSun", "NSimSun", monospace !important;
  white-space: pre !important;
  font-variant-ligatures: none;
}
```

**你想先尝试快速的 CSS 修复，还是让我为你草拟一个基于 Canvas 的渲染组件方案？**


### Opinion from Gemini 3 Pro High (Reasoning)

你好！我是你的 AI 技术顾问。我已经仔细审查了 `docs/design/ascii-rendering-review.md` 文档。

这是一个在 Web 开发中非常经典且棘手的“**中英文混排 ASCII 艺术对齐问题**”。

以下是我基于专业视角的分析与建议报告：

### 1. 核心问题诊断
文档中对问题的界定非常准确：**浏览器的渲染引擎与终端（Terminal）不同**。
*   **终端**：通常强制基于网格（Grid-based）渲染，严格执行“半角字符占1格，全角字符占2格”的规则。
*   **浏览器**：即使设置了 `monospace`（等宽字体），也只保证拉丁字母等宽。对于 CJK（中日韩）字符，浏览器会使用字体栈中的 fallback 字体渲染。如果英文字体宽度是 `X`，中文字体宽度通常不是严格的 `2X`，导致原本在终端里对齐的竖线 `│` 在浏览器里参差不齐。

### 2. 方案评估与点评

针对文档提出的四个方案，我的评估如下：

*   **Option A: Web Font (Sarasa Gothic 等)**
    *   **点评**：🔴 **不推荐作为首选**。
    *   **理由**：包含 CJK 的 Web Font 体积巨大（通常 5MB-20MB+），加载不仅慢，还会导致页面闪烁（FOIT/FOUT）。除非使用非常复杂的后端动态子集化（Dynamic Subsetting）技术，否则对性能伤害太大。

*   **Option B: CSS `font-variant-east-asian`**
    *   **点评**：🔴 **无效**。
    *   **理由**：这个属性主要控制字形变体（如繁体字的具体写法），并不强制字符宽度的几何比例，无法解决 1:2 的对齐问题。

*   **Option C: SVG / Mermaid Conversion (Radical)**
    *   **点评**：🟢 **最佳长期策略**。
    *   **理由**：Web 的原生语言是 DOM 和 SVG，不是 ASCII 字符画。Mermaid 能提供可交互、可缩放、语义化的图表。
    *   **代价**：需要修改 Prompt 工程或引入复杂的 ASCII-to-Structure 转换器，成本较高。

*   **Option D: "Close Enough" CSS Hacks**
    *   **点评**：🟡 **可行的短期妥协**。
    *   **理由**：通过精心调整 `font-family` 顺序，可以在主流系统（Windows/macOS）上达到“勉强可看”的效果，但无法做到像素级完美。

### 3. 专家建议方案

针对“必须在 Web 上渲染现有的 ASCII 架构图”这一需求，我建议采用 **"CSS 优化 + 模拟终端"** 的分层策略：

#### 方案一：高性价比 CSS 优化（立即实施）

不要只写 `monospace`，针对不同操作系统指定“已知宽度比例较好”的字体栈。

```css
.is-diagram .code-block {
  /* 强制不换行，这是必须的 */
  white-space: pre !important; 
  /* 调整行高以连接垂直线条，通常 1.0 - 1.2 之间 */
  line-height: 1.1 !important; 
  
  /* 关键：字体栈策略 */
  font-family: 
    /* 1. 优先尝试本地已安装的“严格半角/全角”字体 (开发者常用) */
    "Sarasa Mono SC", "Sarasa Mono TC", 
    /* 2. Windows 下的新宋体(NSimSun)是少数能严格对齐的中文字体 */
    "Consolas", "NSimSun", "SimSun", 
    /* 3. macOS 下的 fallback */
    "Menlo", "PingFang SC", 
    /* 4. 标准 fallback */
    "Courier New", monospace !important;
}
```
*   **注意**：Windows 的 `NSimSun` (新宋体) 虽然稍显古老，但它的英文和中文宽度比例通常能保持良好的 1:2 关系。

#### 方案二：终极渲染方案 - "DOM Grid" (推荐用于追求完美效果)

如果 CSS Hack 无法满足要求（例如用户非常在意对齐），不要试图让浏览器排版文本，而是**自己排版**。

创建一个 React 组件 `<TerminalRenderer content={...} />`：
1.  **解析**：将文本按行分割 (`\n`)。
2.  **计算**：对于每一行，分析每个字符。
    *   ASCII 字符：渲染为 `width: 8px` 的 `span`。
    *   CJK 字符：渲染为 `width: 16px` 的 `span`。
3.  **渲染**：使用 `display: flex` 或 `grid` 强制拼装。

**伪代码逻辑**：
```jsx
// 强制每个字符占位，模拟终端网格
<div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, 1ch)' }}>
  {chars.map(char => (
    <span style={{ 
      gridColumn: isWide(char) ? 'span 2' : 'span 1',
      fontFamily: 'Menlo, monospace' // 此时字体不再影响对齐，只影响字形
    }}>
      {char}
    </span>
  ))}
</div>
```
*这种方法可以 100% 解决对齐问题，且不需要加载大字体文件。*

### 总结

*   **短期**：请立即尝试更新 `globals.css` 中的字体栈（参考方案一），引入 `NSimSun` 对 Windows 用户会有立竿见影的改善。
*   **长期**：如果这主要用于展示 AI 生成的架构图，**Mermaid (Option C)** 是绝对正确的方向。ASCII 艺术在现代 Web 上始终是二等公民。
