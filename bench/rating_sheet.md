# Sloth benchmark - blind rating sheet

For each pair, pick the message you would commit unedited, and note any claim the message makes that the diff does not support.

## qwen3_0.6b__human__matcha:05e75972726a  (matcha)

**A** `fix: add folder name mapping to mailbox handling`

**B** `fix(jmap): helper to find inbox name (#1736)`

Winner: A / B / neither    Unsupported claim? yes / no    Note: ____

<details><summary>diff</summary>

```
diff --git a/backend/jmap/jmap.go b/backend/jmap/jmap.go
index 29693a5..2b61b9f 100644
--- a/backend/jmap/jmap.go
+++ b/backend/jmap/jmap.go
@@ -102,7 +102,7 @@ func (p *Provider) refreshMailboxes() error {
 	for _, inv := range resp.Responses {
 		if r, ok := inv.Args.(*mailbox.GetResponse); ok {
 			for _, mbox := range r.List {
-				p.mailboxes[mbox.Name] = mbox.ID
+				p.mailboxes[folderName(mbox)] = mbox.ID
 				if mbox.Role != "" {
 					p.roleToID[mbox.Role] = mbox.ID
 				}
@@ -112,6 +112,15 @@ func (p *Provider) refreshMailboxes() error {
 	return nil
 }
 
+// folderName reports the inbox as "INBOX" (servers like Fastmail call it
+// "Inbox") so it matches the folder name the rest of the app uses.
+func folderName(mbox *mailbox.Mailbox) string {
+	if mbox.Role == mailbox.RoleInbox {
+		return "INBOX"
+	}
+	return mbox.Name
+}
+
 // resolveMailboxID maps a folder name to a JMAP mailbox ID.
 func (p *Provider) resolveMailboxID(folder string) (jmapclient.ID, error) {
 	p.mu.Lock()
@@ -670,7 +679,7 @@ func (p *Provider) FetchFolders(_ context.Context) ([]backend.Folder, error) {
 		if r, ok := inv.Args.(*mailbox.GetResponse); ok {
 			for _, mbox := range r.List {
 				folders = append(folders, backend.Folder{
-					Name:      mbox.Name,
+					Name:      folderName(mbox),
 					Delimiter: "/",
 				})
 			}
diff --git a/backend/jmap/jmap_folder_test.go b/backend/jmap/jmap_folder_test.go
new file mode 100644
index 0000000..d06aff8
--- /dev/null
+++ b/backend/jmap/jmap_folder_test.go
@@ -0,0 +1,16 @@
+package jmap
+
+import (
+	"testing"
+
+	"git.sr.ht/~rockorager/go-jmap/mail/mailbox"
+)
+
+func TestFolderName(t *testing.T) {
+	if got := folderName(&mailbox.Mailbox{Name: "Inbox", Role: mailbox.RoleInbox}); got != "INBOX" {
+		t.Errorf("inbox: got %q, want INBOX", got)
+	}
+	if got := folderName(&mailbox.Mailbox{Name: "Work"}); got != "Work" {
+		t.Errorf("folder: got %q, want Work", got)
+	}
+}

```

</details>

## qwen3_0.6b__human__portfolio-website:0af845b174a1  (portfolio-website)

**A** `Fix rate-limiting logic for client IP validation`

**B** `chore(lib): remove orphaned rate limiter`

Winner: A / B / neither    Unsupported claim? yes / no    Note: ____

<details><summary>diff</summary>

```
diff --git a/app/lib/rate-limit.ts b/app/lib/rate-limit.ts
deleted file mode 100644
index 9d4a1cb..0000000
--- a/app/lib/rate-limit.ts
+++ /dev/null
@@ -1,62 +0,0 @@
-import { NextRequest } from "next/server";
-
-type RateLimitConfig = {
-  // Namespace so separate limiters (contact, admin login) don't share buckets
-  name: string;
-  windowMs: number;
-  max: number;
-};
-
-type RateLimitResult =
-  | { allowed: true; retryAfterSeconds: number }
-  | { allowed: false; retryAfterSeconds: number };
-
-// In-memory sliding window, keyed by client IP.
-// Note: on serverless platforms (Vercel) this is per-instance, so it guards
-// against casual spam/brute force but not distributed abuse. For stronger
-// protection use an external counter (e.g. Vercel KV / Upstash Redis).
-const buckets = new Map<string, number[]>();
-
-export function getClientIp(req: NextRequest): string {
-  // Trust model: on Vercel the edge sets x-forwarded-for to the verified
-  // client IP (spoofed values are overwritten — verified live). On other
-  // hosts, only trust this header behind a proxy that strips client input.
-  const forwarded = req.headers.get("x-forwarded-for");
-  if (forwarded) {
-    const first = forwarded.split(",")[0]?.trim();
-    if (first) return first;
-  }
-  return req.headers.get("x-real-ip") ?? "unknown";
-}
-
-export function rateLimit(
-  req: NextRequest,
-  { name, windowMs, max }: RateLimitConfig
-): RateLimitResult {
-  const key = `${name}:${getClientIp(req)}`;
-  const now = Date.now();
-
-  const windowStart = now - windowMs;
-  const timestamps = (buckets.get(key) ?? []).filter((t) => t > windowStart);
-
-  if (timestamps.length >= max) {
-    buckets.set(key, timestamps);
-    const retryAfterSeconds = Math.max(
-      Math.ceil((timestamps[0] + windowMs - now) / 1000),
-      1
-    );
-    return { allowed: false, retryAfterSeconds };
-  }
-
-  timestamps.push(now);
-  buckets.set(key, timestamps);
-
-  // Opportunistic cleanup to avoid unbounded growth
-  if (buckets.size > 10_000) {
-    for (const [k, ts] of buckets) {
-      if (ts[ts.length - 1] < windowStart) buckets.delete(k);
-    }
-  }
-
-  return { allowed: true, retryAfterSeconds: 0 };
-}

```

</details>

## qwen3_0.6b__human__runnix:1afa9025f771  (runnix)

**A** `chore: phase 2 drop speculative metrics endpoint`

**B** `fix: update health check to use metrics`

Winner: A / B / neither    Unsupported claim? yes / no    Note: ____

<details><summary>diff</summary>

```
diff --git a/deploy/Caddyfile b/deploy/Caddyfile
index e0385de..ae8c48c 100644
--- a/deploy/Caddyfile
+++ b/deploy/Caddyfile
@@ -2,12 +2,10 @@
 
 {$API_DOMAIN} {
 	encode gzip
-	handle /metrics* { respond "not found" 404 }
 	handle { reverse_proxy prod-gateway:4000 }
 }
 
 {$DEV_DOMAIN} {
 	encode gzip
-	handle /metrics* { respond "not found" 404 }
 	handle { reverse_proxy dev-gateway:4000 }
 }
diff --git a/go.mod b/go.mod
index de2c3a3..70c4d94 100644
--- a/go.mod
+++ b/go.mod
@@ -9,7 +9,6 @@ require (
 	github.com/google/uuid v1.6.0
 	github.com/jackc/pgx/v5 v5.10.0
 	github.com/nats-io/nats.go v1.53.1
-	github.com/prometheus/client_golang v1.24.1
 	github.com/testcontainers/testcontainers-go v0.44.0
 	github.com/testcontainers/testcontainers-go/modules/nats v0.44.0
 	github.com/testcontainers/testcontainers-go/modules/postgres v0.44.0
@@ -20,7 +19,6 @@ require (
 	dario.cat/mergo v1.0.2 // indirect
 	github.com/Azure/go-ansiterm v0.0.0-20250102033503-faa5f7b0171c // indirect
 	github.com/Microsoft/go-winio v0.6.2 // indirect
-	github.com/beorn7/perks v1.0.1 // indirect
 	github.com/cenkalti/backoff/v4 v4.3.0 // indirect
 	github.com/cespare/xxhash/v2 v2.3.0 // indirect
 	github.com/containerd/errdefs v1.0.0 // indirect
@@ -53,16 +51,12 @@ require (
 	github.com/moby/sys/userns v0.1.0 // indirect
 	github.com/moby/term v0.5.2 // indirect
 	github.com/morikuni/aec v1.1.0 // indirect
-	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
 	github.com/nats-io/nkeys v0.4.15 // indirect
 	github.com/nats-io/nuid v1.0.1 // indirect
 	github.com/opencontainers/go-digest v1.0.0 // indirect
 	github.com/opencontainers/image-spec v1.1.1 // indirect
 	github.com/pkg/errors v0.9.1 // indirect
 	github.com/power-devops/perfstat v0.0.0-20240221224432-82ca36839d55 // indirect
-	github.com/prometheus/client_model v0.6.2 // indirect
-	github.com/prometheus/common v0.70.1 // indirect
-	github.com/prometheus/procfs v0.21.1 // indirect
 	github.com/shirou/gopsutil/v4 v4.26.6 // indirect
 	github.com/sirupsen/logrus v1.9.4 // indirect
 	github.com/stretchr/testify v1.12.1 // indirect
@@ -81,5 +75,4 @@ require (
 	golang.org/x/sync v0.22.0 // indirect
 	golang.org/x/sys v0.47.0 // indirect
 	golang.org/x/text v0.41.0 // indirect
-	google.golang.org/protobuf v1.36.12 // indirect
 )
diff --git a/go.sum b/go.sum
index 7a5dbe3..39ee160 100644
--- a/go.sum
+++ b/go.sum
@@ -6,8 +6,6 @@ github.com/Azure/go-ansiterm v0.0.0-20250102033503-faa5f7b0171c h1:udKWzYgxTojEK
 github.com/Azure/go-ansiterm v0.0.0-20250102033503-faa5f7b0171c/go.mod h1:xomTg63KZ2rFqZQzSB4Vz2SUXa1BpHTVz9L5PTmPC4E=
 github.com/Microsoft/go-winio v0.6.2 h1:F2VQgta7ecxGYO8k3ZZz3RS8fVIXVxONVUPlNERoyfY=
 github.com/Microsoft/go-winio v0.6.2/go.mod h1:yd8OoFMLzJbo9gZq8j5qaps8bJ9aShtEA8Ipt1oGCvU=
-github.com/beorn7/perks v1.0.1 h1:VlbKKnNfV8bJzeqoa4cOKqO6bYr3WgKZxO8Z16+hsOM=
-github.com/beorn7/perks v1.0.1/go.mod h1:G2ZrVWU2WbWT9wwq4/hrbKbnv/1ERSJQ0ibhJ6rlkpw=
 github.com/cenkalti/backoff/v4 v4.3.0 h1:MyRJ/UdXutAwSAT+s3wNd7MfTIcy71VQueUuFK343L8=
 github.com/cenkalti/backoff/v4 v4.3.0/go.mod h1:Y3VNntkOUPxTVeUxJ/G5vcM//AlwfmyYozVcomhLiZE=
 github.com/cenkalti/backoff/v5 v5.0.3 h1:ZN+IMa753KfX5hd8vVaMixjnqRZ3y8CuJKRKj1xcsSM=
@@ -67,8 +65,6 @@ github.com/jackc/puddle/v2 v2.2.2 h1:PR8nw+E/1w0GLuRFSmiioY6UooMp6KJv0/61nB7icHo
 github.com/jackc/puddle/v2 v2.2.2/go.mod h1:vriiEXHvEE654aYKXXjOvZM39qJ0q+azkZFrfEOc3H4=
 github.com/klauspost/compress v1.19.1 h1:VsB4HPswih7mmZ8WleSFQ75c/Ui1M4trX5oAsJnhSlk=
 github.com/klauspost/compress v1.19.1/go.mod h1:cwPg85FWrGar70rWktvGQj8/hthj3wpl0PGDogxkrSQ=
-github.com/kylelemons/godebug v1.1.0 h1:RPNrshWIDI6G2gRW9EHilWtl7Z6Sb1BR0xunSBf0SNc=
-github.com/kylelemons/godebug v1.1.0/go.mod h1:9/0rRGxNHcop5bhtWyNeEfOS8JIWk580+fNqagV/RAw=
 github.com/lib/pq v1.10.9 h1:YXG7RB+JIjhP29X+OtkiDnYaXQwpS4JEWq7dtCCRUEw=
 github.com/lib/pq v1.10.9/go.mod h1:AlVN5x4E4T544tWzH6hKfbfQvm3HdbOxrmggDNAPY9o=
 github.com/lufia/plan9stats v0.0.0-20260330125221-c9
```

</details>

## qwen3_0.6b__human__termchat:7dba37470a8c  (termchat)

**A** `fix(deploy): harden agent attribution and tool attributes`

**B** `chore(docs): update AGENTS.md to never add AI attribution`

Winner: A / B / neither    Unsupported claim? yes / no    Note: ____

<details><summary>diff</summary>

```
diff --git a/AGENTS.md b/AGENTS.md
index b1698ff..8a9bb2a 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -124,6 +124,10 @@ Use `just` commands instead of raw `go` where one exists (see `just --list`).
 - `main` is protected: PR + required checks (`gate / format`,
   `gate / test`). Never push directly.
 - Commit signing is mandatory (`git commit -s -S`).
+- Never add agent attribution. No `Co-authored-by:` trailers for any AI,
+  agent, or tool (Cursor, Claude, OpenCode, Codex, etc.), and no "Generated
+  with" or "Made with" lines in commits, PR or issue titles and bodies, or
+  comments. Strip any that a tool inserts automatically.
 
 ## Deployment
 
diff --git a/doc/uml-diagrams.md b/doc/uml-diagrams.md
new file mode 100644
index 0000000..ddf87de
--- /dev/null
+++ b/doc/uml-diagrams.md
@@ -0,0 +1,123 @@
+# TermChat UML Diagrams
+
+Source: `termchat` vault (21 notes): `01-Overview/Architecture.md`, `01-Overview/Repository-Map.md`, `04-Development/Delivery-and-Deploy.md`, `04-Development/Server-Internals.md`, `04-Development/Concurrency.md` + live code map.
+
+Module: `termchat` (go 1.26.3). No root package. Mains in `cli/` and `server/cmd/server/`.
+
+## 1. Package diagram
+
+```mermaid
+packageDiagram
+package termchat {
+  package shared {
+    + protocol.go : Message, Reaction, UserInfo, RoomInfo
+    + room.go : GenerateRoomCode, NormalizeRoomCode
+    + validation.go : IsValidNickname, IsValidRoomCode
+    + media.go : EncodeMediaFrame, ParseMediaFrame
+    + constants.go
+  }
+  package server {
+    + server.go : StartServer, Stop
+    + websocket.go : readPump, writePump, broadcastToRoom
+    + room.go : Room, roomsMutex
+    + client.go : Client, trySend, close
+    + media.go : token mint/burn, relay
+    + bootstrap.go : render, binaryHandler
+  }
+  package cli <<main>> {
+    + main.go, app.go, tui.go
+    + websocket.go, media.go
+    + lan.go, discover.go
+    + audio.go, video.go
+    + config.go, theme.go, commands.go
+  }
+  package "server/cmd/server" <<main>> {
+    + main.go
+  }
+}
+"server/cmd/server" --> server : imports
+server --> shared : imports
+cli --> shared : imports
+cli ..> server : test-only
+note right of shared : leaf, no internal imports
+```
+
+Deps are acyclic: `cmd/server -> server -> shared`, `cli -> shared`.
+
+## 2. Component diagram
+
+```mermaid
+componentDiagram
+component [CLI Binary\ncli/main] as CLI
+component [TUI\ntui, app, commands] as TUI
+component [WS Conn\nwebsocket, discover] as CONN
+component [Media AV\nmedia, audio, video] as AV
+component [LAN Beacon\nlan 224.0.0.167:9999] as LAN
+component [Server Binary\nserver/cmd/server] as SRV
+component [HTTP Mux\n9 routes] as MUX
+component [Room Registry\nroom.go + roomsMutex] as ROOMS
+component [WS Hub\nreadPump/writePump] as HUB
+component [Media Hub\nmedia.go relay] as MHUB
+component [Bootstrap\nembed sh/ps1 + /bin] as BOOT
+component [Shared Kernel\nprotocol + validation] as SHARED
+component [Caddy\nreverse_proxy :8080] as CADDY
+
+TUI -- CONN : drives
+CONN -- AV : /vc token
+LAN -- CONN : peer list
+CLI -- TUI : contains
+CLI -- CONN : contains
+CLI -- AV : contains
+CLI -- LAN : contains
+CONN --> MUX : GET /ws, /discover
+AV --> MUX : WS /media
+MUX -- HUB : upgrade
+MUX -- MHUB : dial
+MUX -- BOOT : /{room}, /bin/*
+HUB -- ROOMS : get-or-create
+MHUB -- ROOMS : peers-only relay
+SRV -- MUX : contains
+SRV -- ROOMS : contains
+SRV -- HUB : contains
+SRV -- MHUB : contains
+SRV -- BOOT : contains
+CLI ..> SHARED : GenerateRoomCode, Message
+SRV ..> SHARED : Message, RoomInfo
+CADDY --> SRV : websocket:8080
+```
+
+Interfaces: `/ws` chat, `/media` voice/video (6-byte framed PCM+JPEG), `/discover` ONLINE ROOMS, `/{room}` + `/bin/{binary}` bootstrap.
+
+## 3. Deployment diagram
+
+```mermaid
+flowchart TB
+  subgraph GH[GitHub]
+    TAG[tag cli-v*] --> CLIW[cli.yml\n7 binaries + checksums\nldflags Version + DefaultWS]
+    CLIW --> REL[Release\nbinaries + termchat-checksums.txt]
+    CLIW --> AURW[aur.yml\nAUR sync via S
```

</details>

