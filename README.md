# book_source

`book_source` 是一个给前端项目使用的静态素材库，提供封面图、字体文件、字体预览图，以及一个浏览器端 ESM 运行时。

## 接入

发布后，前端只需要同一路径下这几类文件：

- `lib/book-source-client.js`
- `cdn-sources.json`
- `cdn-manifest.json`
- `font-catalog.json`
- `Base_Cover/`
- `Base_Fonts/`
- `Base_Font_Thumbs/`

最小接入示例：

```js
import { createBookSourceClient } from "https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/lib/book-source-client.js";

const client = await createBookSourceClient({
  baseUrl: "https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/"
});

const cover = client.createCoverImageElement("cover_1", "thumb");
const fontCard = client.createFontShowcaseElement("Base_Fonts/SourceHanSansSC-Regular.ttf");

cover.addEventListener("booksource:error", (event) => {
  console.error(event.detail);
});

document.body.append(cover, fontCard);
```

`baseUrl` 可以指向：

- `https://cdn.jsdelivr.net/gh/<owner>/<repo>@main/`
- `https://raw.githubusercontent.com/<owner>/<repo>/main/`
- GitHub Release 中同一套文件的发布根路径

## 运行时能力

常用接口：

- `createBookSourceClient(options)`：创建客户端。
- `listCoversByVariant(variant)`：获取指定类型的封面列表。
- `createCoverImageElement(groupId, variant, options)`：生成封面图元素，默认按可见区懒加载。
- `preloadImages(paths, options)`：按并发限制预热图片。
- `preloadCoverVariants(variant, options)`：批量预热一类封面。
- `listFonts(options)`：获取字体列表，可按状态过滤。
- `resolveFontUrl(font)`：解析字体文件地址。
- `resolveFontLogoUrl(font)`：解析字体预览图地址。
- `loadFontFace(font, options)`：按需加载字体。
- `createFontShowcaseElement(font, options)`：生成字体展示卡片，默认按可见区懒加载。
- `preloadFontLogos(fonts, options)`：批量预热字体预览图。
- `getLimitWarnings()`：读取 jsDelivr 限额告警。

默认加载策略：

- 字体不会因为拿到 URL 就自动下载，只有执行 `loadFontFace()` 或字体卡片进入可见区后才会请求。
- 封面图元素和字体展示卡片默认按可见区懒加载。
- 如果业务需要首屏提速，建议只预热当前页面需要的资源，不要一次性预热整个素材库。

错误处理：

- Promise 接口失败时会抛出 `BookSourceClientError`。
- `error.code` 可用于稳定分支处理，例如 `FONT_LOAD_FAILED`、`IMAGE_LOAD_FAILED`、`IMAGE_PRELOAD_FAILED`。
- `error.details` 会带资源路径、候选 URL 和失败尝试记录。
- 元素接口会派发 `booksource:loadstart`、`booksource:load`、`booksource:error` 事件，适合接兜底图、重试和埋点。

## 构建与发布

素材变更后执行：

```powershell
python .\scripts\build_asset_manifest.py
python .\scripts\build_font_catalog.py
```

或直接执行：

```powershell
npm run build:data
```

会更新这些产物：

- `cdn-manifest.json`
- `font-catalog.json`
- `font-cleanup.md`
- `Base_Font_Thumbs/*.png`
- README 中的授权概览区块

发布要求：

- 运行时、JSON 清单和素材目录必须一起发布。
- 如果改了仓库地址、分支名或镜像源配置，发布前重新构建一次。
- 临时文件统一放在 `temp/`，不要保留在仓库其他目录。

## 授权

根目录 `LICENSE` 使用 MIT，但只覆盖仓库自写代码、脚本、配置、文档和生成元数据，例如：

- `lib/`
- `scripts/`
- `README.md`
- `package.json`
- `cdn-sources.json`
- `cdn-manifest.json`
- `font-catalog.json`
- `font-cleanup.md`

以下目录不属于统一 MIT 授权：

- `Base_Cover/`
- `Base_Fonts/`
- `Base_Font_Thumbs/`

这些素材仍然遵循各自上游来源的版权和许可条款。字体是否可商用，必须按单个字体判断，不能按整个仓库一概而论。

<!-- FONT_COMMERCIAL_STATUS:START -->
### 字体授权概览

以下结果由构建脚本根据字体内嵌许可信息汇总生成，用于工程筛查。
单个字体的详细许可字段请查看 `font-catalog.json` 中的 `fonts[].commercialUse` 和 `fonts[].metadata`。

生产建议：

- `restricted` 不进入正式素材库。
- `unknown` 需要补充来源和授权证明后再使用。
- 需要做严格筛选时，直接基于 `font-catalog.json` 过滤，不建议手工维护名单。

生成时间：`2026-03-27T11:30:57Z`

当前统计：

- `不建议商用（受限）`：14。字体内嵌元数据指向 Apple、Microsoft、厂商或系统字体限制，不建议直接用于商业分发。
- `商用前需复核（Copyleft）`：3。字体内嵌元数据包含 GPL 类 Copyleft 提示，商用前需检查打包、再分发和附带义务。
- `需人工复核（自定义许可）`：5。检测到自定义或非标准许可提示，需要人工查原始协议后再判断能否商用。
- `未知，暂不视为可商用`：56。未检出可靠的内嵌许可信息，本仓库默认不将其视为商用安全字体。
- `倾向可商用（开放许可）`：32。字体内嵌元数据指向 OFL、Apache 或 Arphic 等开放字体许可，但分发时仍需保留要求的许可说明。
<!-- FONT_COMMERCIAL_STATUS:END -->
