# Sid's iLand integration adapter

The island repository already contains `src/data/visualMotion.ts`, `lib/world/marineVideoCatalog.ts`, `lib/world/marineVideoEncounters.ts`, and `VisualMotionGallery.tsx`.

The adventure package **does not modify the existing island repo**. Instead:

1. Review human-labeled event timestamps and confirmed animal identities.
2. Associate the verified event with a stable world object id using `adventure bind ... --approve`.
3. Upload only approved clips/posters to a video origin; prepare HTTPS `media-map.json` from those verified URLs.
4. Run `adventure export --media-map media-map.json --output adventure-manifest.json`.
5. Import the `worldBindings` array through a schema-validated adapter on the island side.
6. Match `worldEntityId` to a stable marine creature, ski marker, or bike-trail point, and select the published source/time range.
7. Preserve the existing proximity and pause/resume behavior; do not auto-open footage just because the player approaches an object.

Example export entry:

```json
{
  "id": "binding-...",
  "worldEntityId": "marine.manta",
  "sourceAssetId": "sha256...",
  "activity": "snorkeling",
  "eventKind": "animal_presence",
  "matchType": "exact",
  "startMs": 5200,
  "endMs": 14400,
  "src": "https://your-cdn/video.mp4",
  "posterSrc": "https://your-cdn/poster.webp"
}
```

Important: `WorldVideoPoint.animal` currently supports only turtle/shark/manta/ray/reef-fish and reef lagoon/kelp. A separate island PR must expand the typed entity taxonomy and map dynamically resolved ids. Do not make all fish use one misleading species label. Media is not automatically uploaded or published by this repository.

## Publisher security and privacy

The public manifest deliberately omits local camera paths, original basenames, GPS, capture dates, and exact private geographic coordinates. Source originals and metadata remain local unless explicitly approved. For downloadable MP4s and HLS, CDN access policy, CORS, and licensing must be reviewed by the website operator.
