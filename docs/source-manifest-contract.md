# 외부 Agent source·manifest 계약

이 문서는 외부 Agent를 Gym에 넣기 전의 P0 입력 경계를 고정한다. 입력은
**GitHub 공개 저장소 URL + 선택적 ref**이며, source 내용은 실행하지 않는다.
owner manifest를 우선 읽고, manifest가 없을 때만 별도 검토된 static metadata
profile을 사용할 수 있다.

## 1. SourceDescriptor

`parse_github_url()`은 다음 URL만 지원한다.

- `https://github.com/{owner}/{repo}` + `ref=branch-or-tag`
- `https://github.com/{owner}/{repo}/tree/{branch-or-tag}`
- `https://github.com/{owner}/{repo}/releases/tag/{tag}`
- `https://github.com/{owner}/{repo}/commit/{sha}`

branch와 tag는 resolver가 full commit SHA로 바꾼다. 결과에는 다음 provenance가
항상 남는다.

```json
{
  "repository": "owner/repo",
  "repository_url": "https://github.com/owner/repo",
  "source_url": "https://github.com/owner/repo/tree/main",
  "requested_ref": "main",
  "resolved_sha": "0123456789abcdef0123456789abcdef01234567",
  "retrieved_at": "2026-08-01T09:00:00+09:00",
  "resolver": "github-api"
}
```

P0은 `github.com`만 허용한다. 잘못된 URL, 미지원 host, 접근 실패는 각각
typed error로 반환하며, source를 clone/import/실행하지 않는다. 테스트에서는
`MappingRevisionResolver`를 주입해 네트워크 없이 동일한 SHA를 재현한다.

## 2. AgentManifest

고정 checkout의 다음 두 경로만 탐색한다(첫 번째가 우선).

- `.agent24/manifest.json`
- `agent24.manifest.json`

최소 예시는 다음과 같다.

```json
{
  "name": "cake-buyer",
  "entrypoint": "agent/main.py",
  "system_prompt": "Buy one cake under budget.",
  "mission_family": "purchase",
  "permissions": {"max_spend_krw": 50000},
  "tools": [
    {"name": "catalog.search"},
    {"name": "payment.charge", "side_effect": true}
  ]
}
```

`load_manifest()`은 full SHA를 가진 `SourceDescriptor` 없이는 동작하지 않는다.
entrypoint는 상대 경로 metadata로만 검증하며 import하거나 setup hook을 실행하지
않는다. manifest bytes의 SHA-256은 `manifest_hash`로 기록되고 adapter version은
`agent24.manifest.v1`로 고정된다.

현재 Life Gym에서 지원하는 tool 외의 이름은 `unsupported_tools`에 남긴다.
중복 tool도 `duplicate:<name>`으로 표시하므로 조용히 덮어쓰지 않는다. malformed
JSON/schema, path traversal, allowlist 밖 파일, symlink는 typed error다.

`unsupported_tools`가 비어 있지 않으면 이를 “지원하지 않는 범위”로 보고하며,
실행 가능한 Agent라고 추정하지 않는다. README 문구만으로 Agent의 안전성이나
행동 성격을 확정하지 않는 규칙은 `BehaviorProfile`의 evidence 검증이 담당한다.

저장소 루트의 `.agent24/manifest.json`은 known-good 데모 AUT 계약이다. private
repository를 입력할 때는 로컬 `.env`의 `GITHUB_TOKEN`을 사용할 수 있으며, 토큰은
GitHub metadata/contents 요청 헤더에만 전달되고 event·JSONL·오류 문구에 포함되지 않는다.

## 3. Manifest 없는 participant source

manifest 부재는 곧바로 오류나 임의의 synthetic fallback을 뜻하지 않는다. exact
`repository@resolved_sha`가 reviewed static registry에 있으면 최대 4개 allowlisted
path의 Git blob SHA·size·object type을 다시 확인해 compatibility profile을 만든다.
owner manifest가 나중에 추가되면 static profile보다 항상 우선한다.

Static profile은 `OWNER MANIFEST`와 혼동하지 않도록
`LAB-INFERRED STATIC PROFILE`로 표시하며 experiments 0, findings 0인 report에서
종료한다. 세부 path policy, audited source, event 계약은
[`participant-repository-intake.md`](participant-repository-intake.md)를 따른다.
