"use client";

import React from "react";

/** A single renderable media item — used for user attachments and tool/agent output. */
export interface MediaItem {
  kind: "image" | "video";
  /** Served URL (preferred). */
  url?: string;
  /** Base64 data fallback (no leading data: prefix). */
  data?: string;
  mimeType?: string;
  name?: string;
}

function mediaSrc(item: MediaItem): string | undefined {
  if (item.url) return item.url;
  if (item.data) return `data:${item.mimeType || "application/octet-stream"};base64,${item.data}`;
  return undefined;
}

/** Render a single image/video tile. */
export function MediaTile({ item, className }: { item: MediaItem; className?: string }) {
  const src = mediaSrc(item);
  if (!src) return null;

  if (item.kind === "video") {
    return (
      <video
        src={src}
        controls
        preload="metadata"
        className={className || "max-w-[320px] max-h-[320px] rounded-xl border border-white/10 shadow-md bg-black"}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={item.name || "media"}
      className={className || "max-w-[240px] max-h-[240px] rounded-xl object-cover border border-white/10 shadow-md"}
    />
  );
}

/**
 * Normalize a tool result's `ui_detail.media` field into MediaItem[].
 * Accepts a single object or an array; tolerates loose shapes from the backend.
 */
export function normalizeMedia(raw: unknown): MediaItem[] {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : [raw];
  const out: MediaItem[] = [];
  for (const m of arr) {
    if (!m || typeof m !== "object") continue;
    const o = m as Record<string, any>;
    const mimeType: string | undefined = o.mimeType || o.mime_type;
    let kind: "image" | "video" | undefined = o.kind;
    if (kind !== "image" && kind !== "video") {
      if (mimeType?.startsWith("video/")) kind = "video";
      else if (mimeType?.startsWith("image/")) kind = "image";
    }
    if (kind !== "image" && kind !== "video") continue;
    const url = o.url;
    const data = o.data || o.content;
    if (!url && !data) continue;
    out.push({ kind, url, data, mimeType, name: o.name });
  }
  return out;
}

/** Grid of media tiles. Returns null when there's nothing to show. */
export function MediaView({ media, className }: { media: MediaItem[]; className?: string }) {
  if (!media || media.length === 0) return null;
  return (
    <div className={className || "flex flex-wrap gap-2"}>
      {media.map((item, i) => (
        <MediaTile key={`${item.url || item.name || "media"}-${i}`} item={item} />
      ))}
    </div>
  );
}
