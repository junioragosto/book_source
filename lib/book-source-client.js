const RAW_GITHUB_RE = /^https?:\/\/raw\.githubusercontent\.com\/([^/]+)\/([^/]+)\/([^/]+)\/(.+)$/i;
const JSDELIVR_GH_RE = /^https?:\/\/cdn\.jsdelivr\.net\/gh\/([^/]+)\/([^@/]+)@([^/]+)\/(.+)$/i;
const GITHUB_RELEASE_RE = /^https?:\/\/github\.com\/([^/]+)\/([^/]+)\/releases\/download\/([^/]+)\/(.+)$/i;
const STYLE_ID = "book-source-client-styles";
const EVENT_LOAD_START = "booksource:loadstart";
const EVENT_LOAD = "booksource:load";
const EVENT_ERROR = "booksource:error";
const LOAD_MODES = new Set(["immediate", "visible", "manual"]);
const DEFAULT_PERFORMANCE = Object.freeze({
  fontLoadMode: "visible",
  imageLoadMode: "visible",
  preloadConcurrency: 4,
  visibilityRootMargin: "240px 0px",
  fontDisplay: "swap",
  fallbackFamily: "sans-serif",
});

export class BookSourceClientError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "BookSourceClientError";
    this.code = options.code ?? "BOOK_SOURCE_ERROR";
    this.details = options.details ?? {};
    if ("cause" in options) {
      this.cause = options.cause;
    }
  }
}

function fail(message, code, details = {}, cause) {
  return new BookSourceClientError(message, { code, details, cause });
}

function serializeError(error) {
  if (error instanceof BookSourceClientError) {
    return { name: error.name, code: error.code, message: error.message, details: error.details };
  }
  if (error && typeof error === "object") {
    return { name: error.name ?? "Error", message: error.message ?? String(error) };
  }
  return { name: "Error", message: String(error) };
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeBaseUrl(baseUrl) {
  return baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
}

function encodePath(path) {
  return path.split("/").map((part) => encodeURIComponent(part)).join("/");
}

function normalizeLoadMode(value, fallback) {
  return LOAD_MODES.has(value) ? value : fallback;
}

function clampPositiveInteger(value, fallback) {
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(1, Math.floor(value));
}

function normalizePerformanceOptions(options = {}) {
  return {
    ...DEFAULT_PERFORMANCE,
    ...options,
    fontLoadMode: normalizeLoadMode(options.fontLoadMode, DEFAULT_PERFORMANCE.fontLoadMode),
    imageLoadMode: normalizeLoadMode(options.imageLoadMode, DEFAULT_PERFORMANCE.imageLoadMode),
    preloadConcurrency: clampPositiveInteger(options.preloadConcurrency, DEFAULT_PERFORMANCE.preloadConcurrency),
  };
}

async function fetchJson(url) {
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    throw fail(`Failed to fetch ${url}`, "JSON_FETCH_FAILED", { url, stage: "request", error: serializeError(error) }, error);
  }
  if (!response.ok) {
    throw fail(`Failed to fetch ${url}: ${response.status} ${response.statusText}`, "JSON_FETCH_FAILED", {
      url,
      stage: "response",
      status: response.status,
      statusText: response.statusText,
    });
  }
  try {
    return await response.json();
  } catch (error) {
    throw fail(`Invalid JSON from ${url}`, "JSON_PARSE_FAILED", { url, stage: "parse", error: serializeError(error) }, error);
  }
}

async function runWithConcurrency(taskFactories, concurrency) {
  if (taskFactories.length === 0) {
    return [];
  }
  const limit = Math.min(clampPositiveInteger(concurrency, 1), taskFactories.length);
  const results = new Array(taskFactories.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < taskFactories.length) {
      const current = nextIndex;
      nextIndex += 1;
      results[current] = await taskFactories[current]();
    }
  }
  await Promise.all(Array.from({ length: limit }, () => worker()));
  return results;
}

function ensureBrowserCapability(name, value) {
  if (!value) {
    throw fail(`${name} is required in a browser environment.`, "BROWSER_CAPABILITY_REQUIRED", { name });
  }
}

function dispatchResourceEvent(target, type, detail) {
  if (!target || typeof target.dispatchEvent !== "function" || typeof CustomEvent === "undefined") {
    return;
  }
  target.dispatchEvent(new CustomEvent(type, { detail }));
}

function updateElementState(target, patch) {
  const nextState = { ...(target.bookSourceState ?? {}), ...patch };
  target.bookSourceState = nextState;
  target.dataset.loadState = nextState.status ?? "idle";
  if (nextState.kind) {
    target.dataset.resourceKind = nextState.kind;
  }
  if (nextState.path) {
    target.dataset.resourcePath = nextState.path;
  }
  if (nextState.errorCode) {
    target.dataset.errorCode = nextState.errorCode;
  }
  if (nextState.errorMessage) {
    target.dataset.errorMessage = nextState.errorMessage;
  }
  return nextState;
}

function observeWhenVisible(target, callback, options = {}) {
  if (typeof IntersectionObserver === "undefined") {
    callback();
    return () => {};
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting || entry.intersectionRatio > 0) {
          observer.disconnect();
          callback();
          return;
        }
      }
    },
    {
      root: options.root ?? null,
      rootMargin: options.rootMargin ?? DEFAULT_PERFORMANCE.visibilityRootMargin,
      threshold: options.threshold ?? 0.01,
    },
  );
  observer.observe(target);
  return () => observer.disconnect();
}

function attachDeferredLoad(target, load, options = {}) {
  const loadMode = normalizeLoadMode(options.loadMode, "visible");
  let observerCleanup = () => {};
  const onInteraction = () => {
    void start();
  };
  const detachInteraction = () => {
    target.removeEventListener("pointerenter", onInteraction);
    target.removeEventListener("focusin", onInteraction);
  };
  const start = () => {
    observerCleanup();
    detachInteraction();
    return load();
  };
  target.bookSourceLoad = start;
  target.bookSourceLoadMode = loadMode;
  if (loadMode === "immediate") {
    queueMicrotask(() => {
      void start();
    });
    return start;
  }
  if (loadMode === "visible") {
    target.addEventListener("pointerenter", onInteraction, { once: true });
    target.addEventListener("focusin", onInteraction, { once: true });
    observerCleanup = observeWhenVisible(target, () => {
      void start();
    }, options);
  }
  return start;
}

function loadImageIntoElement(element, url, options = {}) {
  if (element.src === url && element.complete && element.naturalWidth > 0) {
    return Promise.resolve({ element, url });
  }
  if (options.loading) {
    element.loading = options.loading;
  }
  if (options.decoding) {
    element.decoding = options.decoding;
  }
  if (options.referrerPolicy) {
    element.referrerPolicy = options.referrerPolicy;
  }
  if ("fetchPriority" in element && options.fetchPriority) {
    element.fetchPriority = options.fetchPriority;
  }
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      element.removeEventListener("load", handleLoad);
      element.removeEventListener("error", handleError);
    };
    const handleLoad = () => {
      cleanup();
      resolve({ element, url });
    };
    const handleError = () => {
      cleanup();
      reject(fail(`Failed to load image ${url}`, "IMAGE_REQUEST_FAILED", { url }));
    };
    element.addEventListener("load", handleLoad, { once: true });
    element.addEventListener("error", handleError, { once: true });
    element.src = url;
  });
}

function preloadImageUrl(url, options = {}) {
  ensureBrowserCapability("Image", typeof Image !== "undefined");
  return new Promise((resolve, reject) => {
    const image = new Image();
    if (options.decoding) {
      image.decoding = options.decoding;
    }
    if (options.referrerPolicy) {
      image.referrerPolicy = options.referrerPolicy;
    }
    if ("fetchPriority" in image && options.fetchPriority) {
      image.fetchPriority = options.fetchPriority;
    }
    image.onload = () => resolve({ url });
    image.onerror = () => reject(fail(`Failed to preload image ${url}`, "IMAGE_REQUEST_FAILED", { url }));
    image.src = url;
  });
}

async function tryCandidateUrls(urls, runAttempt) {
  const attempts = [];
  let lastError = null;
  for (const url of urls) {
    try {
      const value = await runAttempt(url);
      attempts.push({ url, ok: true });
      return { ok: true, url, value, attempts };
    } catch (error) {
      lastError = error;
      attempts.push({ url, ok: false, error: serializeError(error) });
    }
  }
  return { ok: false, url: null, value: null, attempts, lastError };
}

function injectStyles() {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) {
    return;
  }
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
.book-source-font-showcase {
  display: grid;
  gap: 12px;
  width: min(100%, 320px);
  padding: 16px;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 16px;
  background: linear-gradient(180deg, #ffffff 0%, #f7f7f7 100%);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.06);
}
.book-source-font-showcase__logo {
  width: 100%;
  border-radius: 12px;
  background: #fff;
}
.book-source-font-showcase__sample {
  font-size: 32px;
  line-height: 1.1;
  opacity: 0.4;
  transform: translateY(6px);
  transition: opacity 220ms ease, transform 220ms ease;
}
.book-source-font-showcase[data-load-state="loaded"] .book-source-font-showcase__sample {
  opacity: 1;
  transform: translateY(0);
}
.book-source-font-showcase__meta {
  color: #666;
  font-size: 14px;
}`;
  document.head.appendChild(style);
}

export function inferGitContextFromUrl(url) {
  const rawMatch = url.match(RAW_GITHUB_RE);
  if (rawMatch) {
    return {
      git_owner: rawMatch[1],
      git_repo: rawMatch[2],
      git_branch: rawMatch[3],
      git_commit: rawMatch[3],
      git_origin: `https://github.com/${rawMatch[1]}/${rawMatch[2]}.git`,
    };
  }
  const jsdelivrMatch = url.match(JSDELIVR_GH_RE);
  if (jsdelivrMatch) {
    return {
      git_owner: jsdelivrMatch[1],
      git_repo: jsdelivrMatch[2],
      git_branch: jsdelivrMatch[3],
      git_commit: jsdelivrMatch[3],
      git_origin: `https://github.com/${jsdelivrMatch[1]}/${jsdelivrMatch[2]}.git`,
    };
  }
  const releaseMatch = url.match(GITHUB_RELEASE_RE);
  if (releaseMatch) {
    return {
      git_owner: releaseMatch[1],
      git_repo: releaseMatch[2],
      git_branch: releaseMatch[3],
      git_commit: releaseMatch[3],
      git_origin: `https://github.com/${releaseMatch[1]}/${releaseMatch[2]}.git`,
    };
  }
  return {};
}

export function resolveConfigVariables(config, configUrl, overrides = {}) {
  const inferred = configUrl ? inferGitContextFromUrl(configUrl) : {};
  const variables = {};
  const base = config.variables ?? {};
  for (const [key, value] of Object.entries(base)) {
    variables[key] = value === "auto" ? overrides[key] ?? inferred[key] ?? "" : overrides[key] ?? value;
  }
  for (const [key, value] of Object.entries(overrides)) {
    if (!(key in variables)) {
      variables[key] = value;
    }
  }
  return variables;
}

function resolveSourceVariables(source, baseVariables, overrides = {}) {
  const variables = { ...baseVariables };
  for (const [key, value] of Object.entries(source.variables ?? {})) {
    variables[key] = overrides[key] ?? value;
  }
  return variables;
}

function fillTemplate(template, variables) {
  return template.replace(/\{([^}]+)\}/g, (_, key) => {
    if (!(key in variables)) {
      throw fail(`Missing template variable: ${key}`, "MISSING_TEMPLATE_VARIABLE", { key, template });
    }
    return String(variables[key]);
  });
}

function indexBy(items, keyFn) {
  const map = new Map();
  for (const item of items) {
    map.set(keyFn(item), item);
  }
  return map;
}

export class BookSourceClient {
  static async create(options = {}) {
    const {
      baseUrl,
      config,
      configUrl,
      configOverrides = {},
      assetManifest,
      assetManifestUrl,
      fontCatalog,
      fontCatalogUrl,
      performance = {},
    } = options;
    const normalizedBaseUrl = baseUrl ? normalizeBaseUrl(baseUrl) : null;
    const resolvedAssetManifestUrl = assetManifestUrl ?? (normalizedBaseUrl ? new URL("cdn-manifest.json", normalizedBaseUrl).href : null);
    const loadedAssetManifest = assetManifest ?? (resolvedAssetManifestUrl ? await fetchJson(resolvedAssetManifestUrl) : null);
    if (!loadedAssetManifest) {
      throw fail("Asset manifest or assetManifestUrl is required.", "ASSET_MANIFEST_REQUIRED");
    }
    const resolvedFontCatalogUrl = fontCatalogUrl ?? (normalizedBaseUrl ? new URL("font-catalog.json", normalizedBaseUrl).href : null);
    const loadedFontCatalog = fontCatalog ?? (resolvedFontCatalogUrl ? await fetchJson(resolvedFontCatalogUrl) : null);
    if (!loadedFontCatalog) {
      throw fail("Font catalog or fontCatalogUrl is required.", "FONT_CATALOG_REQUIRED");
    }
    const inferredConfigUrl =
      configUrl ??
      (normalizedBaseUrl
        ? new URL(loadedAssetManifest.resolver?.configPath ?? "cdn-sources.json", normalizedBaseUrl).href
        : resolvedAssetManifestUrl
          ? new URL(loadedAssetManifest.resolver?.configPath ?? "cdn-sources.json", resolvedAssetManifestUrl).href
          : null);
    const loadedConfig = config ?? (inferredConfigUrl ? await fetchJson(inferredConfigUrl) : null);
    if (!loadedConfig) {
      throw fail("Source config or configUrl is required.", "SOURCE_CONFIG_REQUIRED");
    }
    return new BookSourceClient({
      config: loadedConfig,
      configUrl: inferredConfigUrl,
      configOverrides,
      assetManifest: loadedAssetManifest,
      assetManifestUrl: resolvedAssetManifestUrl,
      fontCatalog: loadedFontCatalog,
      fontCatalogUrl: resolvedFontCatalogUrl,
      performance,
    });
  }

  static async fromReleaseBase(baseUrl, options = {}) {
    return BookSourceClient.create({ baseUrl, ...options });
  }

  constructor({ config, configUrl, configOverrides, assetManifest, assetManifestUrl, fontCatalog, fontCatalogUrl, performance }) {
    this.config = cloneJson(config);
    this.configUrl = configUrl ?? null;
    this.assetManifest = cloneJson(assetManifest);
    this.assetManifestUrl = assetManifestUrl ?? null;
    this.fontCatalog = cloneJson(fontCatalog);
    this.fontCatalogUrl = fontCatalogUrl ?? null;
    this.variables = resolveConfigVariables(this.config, this.configUrl, configOverrides);
    this.performance = normalizePerformanceOptions(performance);
    this.assetMap = indexBy(this.assetManifest.assets ?? [], (item) => item.path);
    this.fontMap = indexBy(this.fontCatalog.fonts ?? [], (item) => item.path);
    this.coverGroupMap = indexBy(this.assetManifest.covers?.groups ?? [], (item) => item.id);
    this.fontFaceCache = new Map();
    this.fontFaceInflight = new Map();
    this.imagePreloadCache = new Map();
  }

  getPerformanceOptions() {
    return { ...this.performance };
  }

  getEnabledSources() {
    return (this.config.sources ?? []).filter((source) => source.enabled !== false);
  }

  getSource(sourceId) {
    const source = (this.config.sources ?? []).find((item) => item.id === sourceId);
    if (!source) {
      throw fail(`Unknown source id: ${sourceId}`, "UNKNOWN_SOURCE", { sourceId });
    }
    return source;
  }

  resolvePath(path, options = {}) {
    const sourceId = options.sourceId ?? this.getEnabledSources()[0]?.id;
    if (!sourceId) {
      throw fail("No enabled sources available.", "NO_ENABLED_SOURCE");
    }
    const source = this.getSource(sourceId);
    const variables = resolveSourceVariables(source, this.variables, options.variables ?? {});
    return fillTemplate(source.urlTemplate, { ...variables, path, encoded_path: encodePath(path) });
  }

  resolvePathCandidates(path, options = {}) {
    const sources = options.sourceIds ? options.sourceIds.map((sourceId) => this.getSource(sourceId)) : this.getEnabledSources();
    if (sources.length === 0) {
      throw fail("No enabled sources available.", "NO_ENABLED_SOURCE");
    }
    return sources.map((source) => this.resolvePath(path, { ...options, sourceId: source.id }));
  }

  getAsset(path) {
    return this.assetMap.get(path) ?? null;
  }

  getCoverGroup(groupId) {
    return this.coverGroupMap.get(groupId) ?? null;
  }

  listCoverGroups(options = {}) {
    const groups = this.assetManifest.covers?.groups ?? [];
    return options.variant ? groups.filter((group) => Boolean(group.variants?.[options.variant])) : groups;
  }

  listCoversByVariant(variant) {
    return this.assetManifest.covers?.variants?.[variant] ?? [];
  }

  resolveCoverVariant(groupOrId, variant = "default", options = {}) {
    const group = typeof groupOrId === "string" ? this.getCoverGroup(groupOrId) : groupOrId;
    if (!group) {
      throw fail(`Unknown cover group: ${groupOrId}`, "UNKNOWN_COVER_GROUP", { groupOrId });
    }
    const path = group.variants?.[variant];
    if (!path) {
      throw fail(`Cover variant '${variant}' not found for group '${group.id}'.`, "COVER_VARIANT_NOT_FOUND", {
        groupId: group.id,
        variant,
      });
    }
    return { path, urls: this.resolvePathCandidates(path, options), url: this.resolvePath(path, options), group, variant };
  }

  async preloadImages(paths, options = {}) {
    const uniquePaths = [...new Set((paths ?? []).filter((item) => typeof item === "string" && item))];
    const tasks = uniquePaths.map((path) => async () => {
      const urls = this.resolvePathCandidates(path, options);
      const attempt = await tryCandidateUrls(urls, async (url) => {
        if (!this.imagePreloadCache.has(url)) {
          this.imagePreloadCache.set(
            url,
            preloadImageUrl(url, {
              decoding: options.decoding ?? "async",
              referrerPolicy: options.referrerPolicy,
              fetchPriority: options.fetchPriority,
            }).catch((error) => {
              this.imagePreloadCache.delete(url);
              throw error;
            }),
          );
        }
        return this.imagePreloadCache.get(url);
      });
      if (!attempt.ok) {
        const error = fail(`Failed to preload image: ${path}`, "IMAGE_PRELOAD_FAILED", {
          type: "image",
          path,
          urls,
          attempts: attempt.attempts,
        }, attempt.lastError);
        if (options.throwOnError) {
          throw error;
        }
        return { ok: false, path, urls, attempts: attempt.attempts, error };
      }
      return { ok: true, path, url: attempt.url, urls, attempts: attempt.attempts };
    });
    return runWithConcurrency(tasks, clampPositiveInteger(options.concurrency, this.performance.preloadConcurrency));
  }

  preloadCoverVariants(variant, options = {}) {
    return this.preloadImages(this.listCoversByVariant(variant), options);
  }

  preloadFontLogos(fonts = null, options = {}) {
    const fontList = fonts ?? this.listFonts();
    const paths = fontList
      .map((entry) => this.getFont(entry))
      .filter(Boolean)
      .map((font) => font.logoPath ?? font.thumbnailPath)
      .filter(Boolean);
    return this.preloadImages(paths, options);
  }

  createCoverImageElement(groupOrId, variant = "default", options = {}) {
    ensureBrowserCapability("document", typeof document !== "undefined");
    const resolved = this.resolveCoverVariant(groupOrId, variant, options);
    const image = document.createElement("img");
    const state = { promise: null };
    const loadMode = normalizeLoadMode(options.loadMode, this.performance.imageLoadMode);
    if (options.className) {
      image.className = options.className;
    }
    image.alt = options.alt ?? resolved.group?.id ?? resolved.path;
    if (options.width) {
      image.width = options.width;
    }
    if (options.height) {
      image.height = options.height;
    }
    image.loading = options.loading ?? (loadMode === "immediate" ? "eager" : "lazy");
    image.decoding = options.decoding ?? "async";
    updateElementState(image, { kind: "cover-image", status: "idle", path: resolved.path, variant, urls: resolved.urls });
    const load = async () => {
      if (state.promise) {
        return state.promise;
      }
      updateElementState(image, { status: "loading" });
      dispatchResourceEvent(image, EVENT_LOAD_START, { type: "image", path: resolved.path, variant, urls: resolved.urls });
      state.promise = (async () => {
        const attempt = await tryCandidateUrls(resolved.urls, (url) =>
          loadImageIntoElement(image, url, {
            loading: image.loading,
            decoding: image.decoding,
            referrerPolicy: options.referrerPolicy,
            fetchPriority: options.fetchPriority,
          }),
        );
        if (!attempt.ok) {
          const error = fail(`Failed to load image: ${resolved.path}`, "IMAGE_LOAD_FAILED", {
            type: "image",
            path: resolved.path,
            variant,
            urls: resolved.urls,
            attempts: attempt.attempts,
          }, attempt.lastError);
          updateElementState(image, { status: "error", errorCode: error.code, errorMessage: error.message });
          dispatchResourceEvent(image, EVENT_ERROR, { ...error.details, error: serializeError(error) });
          throw error;
        }
        const detail = { type: "image", path: resolved.path, variant, url: attempt.url, urls: resolved.urls, attempts: attempt.attempts, element: image };
        updateElementState(image, { status: "loaded", url: attempt.url });
        dispatchResourceEvent(image, EVENT_LOAD, detail);
        return detail;
      })();
      try {
        return await state.promise;
      } catch (error) {
        state.promise = null;
        throw error;
      }
    };
    attachDeferredLoad(image, load, {
      loadMode,
      root: options.root,
      rootMargin: options.rootMargin ?? this.performance.visibilityRootMargin,
      threshold: options.threshold,
    });
    return image;
  }

  listFonts(options = {}) {
    let fonts = this.fontCatalog.fonts ?? [];
    if (options.licenseStatus) {
      fonts = fonts.filter((font) => font.metadata?.licenseStatus === options.licenseStatus);
    }
    if (options.family) {
      const needle = options.family.toLowerCase();
      fonts = fonts.filter((font) => `${font.metadata?.family ?? ""} ${font.metadata?.fullName ?? ""}`.toLowerCase().includes(needle));
    }
    if (options.singleFileExceeded) {
      fonts = fonts.filter((font) => font.limitFlags?.["jsdelivr-gh"]?.singleFileExceeded);
    }
    return fonts;
  }

  getFont(fontOrPath) {
    if (isPlainObject(fontOrPath)) {
      return fontOrPath;
    }
    if (typeof fontOrPath !== "string") {
      return null;
    }
    if (this.fontMap.has(fontOrPath)) {
      return this.fontMap.get(fontOrPath);
    }
    return (
      (this.fontCatalog.fonts ?? []).find((font) => {
        return font.metadata?.fullName === fontOrPath || font.metadata?.postscriptName === fontOrPath || font.metadata?.family === fontOrPath;
      }) ?? null
    );
  }

  resolveFontUrl(fontOrPath, options = {}) {
    const font = this.getFont(fontOrPath);
    if (!font) {
      throw fail(`Unknown font: ${fontOrPath}`, "UNKNOWN_FONT", { fontOrPath });
    }
    return this.resolvePath(font.path, options);
  }

  resolveFontLogoUrl(fontOrPath, options = {}) {
    const font = this.getFont(fontOrPath);
    if (!font) {
      throw fail(`Unknown font: ${fontOrPath}`, "UNKNOWN_FONT", { fontOrPath });
    }
    return this.resolvePath(font.logoPath ?? font.thumbnailPath, options);
  }

  async loadFontFace(fontOrPath, options = {}) {
    ensureBrowserCapability("FontFace", typeof FontFace !== "undefined");
    ensureBrowserCapability("document.fonts", typeof document !== "undefined" && document.fonts);
    const font = this.getFont(fontOrPath);
    if (!font) {
      throw fail(`Unknown font: ${fontOrPath}`, "UNKNOWN_FONT", { fontOrPath });
    }
    const familyName =
      options.familyName ??
      font.metadata?.postscriptName ??
      font.metadata?.fullName ??
      font.metadata?.family ??
      font.path.replace(/[^A-Za-z0-9_-]+/g, "-");
    const descriptors = { display: options.display ?? this.performance.fontDisplay, ...(options.descriptors ?? {}) };
    const candidateUrls = this.resolvePathCandidates(font.path, options);
    const cacheKey = JSON.stringify({
      path: font.path,
      familyName,
      sourceId: options.sourceId ?? null,
      sourceIds: options.sourceIds ?? null,
      variables: options.variables ?? null,
      descriptors,
    });
    if (this.fontFaceCache.has(cacheKey)) {
      return this.fontFaceCache.get(cacheKey);
    }
    if (this.fontFaceInflight.has(cacheKey)) {
      return this.fontFaceInflight.get(cacheKey);
    }
    const promise = (async () => {
      const attempt = await tryCandidateUrls(candidateUrls, async (url) => {
        const fontFace = new FontFace(familyName, `url("${url}")`, descriptors);
        await fontFace.load();
        document.fonts.add(fontFace);
        return fontFace;
      });
      if (!attempt.ok) {
        throw fail(`Failed to load font: ${font.path}`, "FONT_LOAD_FAILED", {
          type: "font",
          path: font.path,
          familyName,
          urls: candidateUrls,
          attempts: attempt.attempts,
        }, attempt.lastError);
      }
      const result = { font, fontFace: attempt.value, familyName, url: attempt.url, urls: candidateUrls, attempts: attempt.attempts };
      this.fontFaceCache.set(cacheKey, result);
      return result;
    })();
    this.fontFaceInflight.set(cacheKey, promise);
    try {
      return await promise;
    } finally {
      this.fontFaceInflight.delete(cacheKey);
    }
  }

  createFontShowcaseElement(fontOrPath, options = {}) {
    ensureBrowserCapability("document", typeof document !== "undefined");
    injectStyles();
    const font = this.getFont(fontOrPath);
    if (!font) {
      throw fail(`Unknown font: ${fontOrPath}`, "UNKNOWN_FONT", { fontOrPath });
    }
    const root = document.createElement("div");
    const state = { promise: null };
    root.className = options.className ? `book-source-font-showcase ${options.className}` : "book-source-font-showcase";
    const logo = document.createElement("img");
    logo.className = "book-source-font-showcase__logo";
    logo.alt = font.metadata?.fullName ?? font.path;
    logo.loading = options.logoLoading ?? "lazy";
    logo.decoding = options.logoDecoding ?? "async";
    const sample = document.createElement("div");
    sample.className = "book-source-font-showcase__sample";
    sample.textContent = options.sampleText ?? this.fontCatalog.sampleText ?? "\u6c38\u548cABC123";
    const meta = document.createElement("div");
    meta.className = "book-source-font-showcase__meta";
    meta.textContent = font.metadata?.fullName ?? font.metadata?.family ?? font.path;
    root.append(logo, sample, meta);
    updateElementState(root, { kind: "font-showcase", status: "idle", path: font.path });
    updateElementState(logo, { kind: "font-logo", status: "idle", path: font.logoPath ?? font.thumbnailPath });
    const logoUrls = this.resolvePathCandidates(font.logoPath ?? font.thumbnailPath, options);
    const loadLogo = async () => {
      const attempt = await tryCandidateUrls(logoUrls, (url) =>
        loadImageIntoElement(logo, url, {
          loading: logo.loading,
          decoding: logo.decoding,
          referrerPolicy: options.referrerPolicy,
          fetchPriority: options.logoFetchPriority,
        }),
      );
      if (!attempt.ok) {
        const error = fail(`Failed to load font logo: ${font.path}`, "FONT_LOGO_LOAD_FAILED", {
          type: "font-logo",
          fontPath: font.path,
          path: font.logoPath ?? font.thumbnailPath,
          urls: logoUrls,
          attempts: attempt.attempts,
        }, attempt.lastError);
        updateElementState(logo, { status: "error", errorCode: error.code, errorMessage: error.message });
        dispatchResourceEvent(root, EVENT_ERROR, { ...error.details, error: serializeError(error) });
        return null;
      }
      updateElementState(logo, { status: "loaded", url: attempt.url });
      return { path: font.logoPath ?? font.thumbnailPath, url: attempt.url, urls: logoUrls, attempts: attempt.attempts };
    };
    const load = async () => {
      if (state.promise) {
        return state.promise;
      }
      updateElementState(root, { status: "loading" });
      dispatchResourceEvent(root, EVENT_LOAD_START, { type: "font-showcase", path: font.path });
      state.promise = (async () => {
        const [logoResult, fontResult] = await Promise.all([loadLogo(), this.loadFontFace(font, options)]).catch((error) => {
          const wrappedError =
            error instanceof BookSourceClientError
              ? error
              : fail(`Failed to load font showcase: ${font.path}`, "FONT_SHOWCASE_LOAD_FAILED", {
                  type: "font-showcase",
                  path: font.path,
                  error: serializeError(error),
                }, error);
          updateElementState(root, { status: "error", errorCode: wrappedError.code, errorMessage: wrappedError.message });
          dispatchResourceEvent(root, EVENT_ERROR, { ...(wrappedError.details ?? {}), error: serializeError(wrappedError) });
          throw wrappedError;
        });
        sample.style.fontFamily = `"${fontResult.familyName}", ${options.fallbackFamily ?? this.performance.fallbackFamily}`;
        const detail = {
          type: "font-showcase",
          path: font.path,
          familyName: fontResult.familyName,
          url: fontResult.url,
          urls: fontResult.urls,
          attempts: fontResult.attempts,
          logo: logoResult,
          element: root,
        };
        updateElementState(root, { status: "loaded", url: fontResult.url, familyName: fontResult.familyName });
        dispatchResourceEvent(root, EVENT_LOAD, detail);
        return detail;
      })();
      try {
        return await state.promise;
      } catch (error) {
        state.promise = null;
        throw error;
      }
    };
    attachDeferredLoad(root, load, {
      loadMode: normalizeLoadMode(options.loadMode, this.performance.fontLoadMode),
      root: options.root,
      rootMargin: options.rootMargin ?? this.performance.visibilityRootMargin,
      threshold: options.threshold,
    });
    return root;
  }

  getLimitWarnings() {
    const assetWarnings = (this.assetManifest.assets ?? []).filter((asset) => asset.limitFlags?.["jsdelivr-gh"]?.singleFileExceeded);
    const fontWarnings = (this.fontCatalog.fonts ?? []).filter((font) => font.limitFlags?.["jsdelivr-gh"]?.singleFileExceeded);
    return {
      assetPackage: this.assetManifest.limitSummary ?? {},
      fontPackage: this.fontCatalog.limitSummary ?? {},
      assets: assetWarnings,
      fonts: fontWarnings,
    };
  }
}

export async function createBookSourceClient(options = {}) {
  return BookSourceClient.create(options);
}
