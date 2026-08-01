# 공개 참가 저장소 intake 계약

이 문서는 이슈 #62의 구현 계약이다. 공개 GitHub 저장소를 full commit SHA로
고정한 뒤, 저장소 코드를 clone/import/install/execute하지 않고 DomainPack
**compatibility candidate**만 만든다. 이 단계는 취약점 진단이나 안전 인증이 아니다.

## 한 입력 흐름

```text
repository URL + ref + mission
  → SourceDescriptor(full SHA)
  → allowlisted owner manifest 탐색
  → OWNER MANIFEST 또는 LAB-INFERRED STATIC PROFILE
  → source_snapshot → target_profile → pack_selection(compatibility hand-off)
  → owner manifest이면 behavior_profile → pack.selected(canonical router)
  → compatibility_report 또는 기존 synthetic diagnosis
```

owner manifest가 있으면 기존 schema validator로 읽고 entrypoint는 실행하지 않는다.
manifest가 없을 때만 exact `repository@SHA`에 등록된 static profile을 검토한다.
등록되지 않은 SHA, 바뀐 blob, 부족한 evidence는 다른 profile이나 비슷한 attack으로
대체하지 않고 `unsupported`로 끝낸다.

## Provenance 모델

`TargetProfileProvenance`는 다음 필드를 Raw Stream과 report에 그대로 남긴다.

- `origin`: `owner_manifest` 또는 `lab_static_profile`
- `repository_url`, full `resolved_sha`
- `generated_at`, static profile의 `reviewed_at`, `adapter_version`
- public visibility와 확인 가능한 license SPDX
- evidence별 repository/path/Git blob SHA/line selector
- 근거가 없는 `unknown_fields`와 지원하지 않는 `unsupported_fields`

화면 레이블은 각각 `OWNER MANIFEST`, `LAB-INFERRED STATIC PROFILE`이다. 후자는
저장소 소유자의 선언이 아니라 Lab이 제한된 metadata를 사람이 검토해 만든
compatibility 가설임을 뜻한다.

## Bounded evidence policy

Static profile은 registry에 명시된 경로만 GitHub Git Trees API에서 directory별
metadata를 따라가며 확인한다. blob contents endpoint는 호출하지 않으므로 파일 본문은
network response, runtime artifact, event에 들어오지 않는다.

| 제한 | 값 |
|---|---:|
| profile당 unique file | 최대 4개 |
| file 크기 | 최대 64,000 bytes |
| 전체 크기 | 최대 128,000 bytes |
| 허용 종류 | README, architecture, 명시적 tool schema, prompt/config |
| 거부 | 절대경로, `..`, 비정규 경로, 과도한 depth, secret-like 이름, binary 확장자 |
| object type | regular file만 허용; symlink/submodule/directory 거부 |

Runtime은 exact full SHA를 `ref`로 보내 path, blob SHA, size, object type만 읽는다.
검토 시점의 blob SHA/size와 하나라도 다르면 profile을 재사용하지 않는다.

## 2026-08-01 pinned registry

아래 표에는 코드나 prompt 본문을 복제하지 않고, 검토한 locator만 기록한다.

| Target | Pinned SHA | Evidence locator | 결과 |
|---|---|---|---|
| `ljh8450/Agent24` | `dd8af1a00ff9229a3d589be5db500d983e4636df` | `README.md@9aa8bf…#L1-L14`, `app/contracts.py@2bdc16…#L118-L134` | Research candidate |
| `midwestchekhov/agent24` | `bf6c545b6c3fd547819b76f9d3d96c5995d43eb0` | `README.md@cd883e…#L1-L4`, `playground/pipeline.py@b8aea2…#L32-L50` | Research candidate |
| `youkim1028/agent24-creative-agent` | `f5527715e6ab6ef1f0ce7b2e098a5d181b5e26cb` | `README.md@152df4…#L50-L58`, `docs/architecture.md@2da875…#L21-L35` | generic starter, unsupported |

E2P와 Paper Playground는 pinned documentation/schema가 research-like source,
claim, provenance, external-evidence surface를 명시하므로 Research candidate다.
이는 ResearchGym에서 실패했다는 뜻이 아니다. generic starter는 자체 문서가 아직
domain-specific tool/prompt를 교체해야 한다고 명시하므로 임의 domain을 추측하지 않는다.

Issue discovery snapshot에서는 `kitavkdl/developAgent`와
`illlilililiililll/YAI-Hackathon`이 empty 후보였다. 완료 감사 시점에는 전자가
`87649e99abf99525b83da14176e4ba16d6ab2d14`로 초기화됐지만 아직 reviewed profile이
없으므로 `pinned_profile_not_registered`로 종료한다. 후자는 계속 commit이 없어
immutable `SourceDescriptor`를 만들지 못한다. 어느 경우도 profile/finding을 추측하지
않는다.

## Event와 terminal report

Manifest 없는 registered participant의 한 입력 run은 같은 `run_id`와 연속 `seq`로
다음 event를 남긴다.

```text
run_started
→ phase.changed(CLONE)
→ source_descriptor
→ source_snapshot
→ target_profile
→ pack_selection
→ compatibility_report
→ run_completed
```

`source_snapshot`은 실제로 가져온 범위를 기록한다. owner manifest 경로는 bounded download와
content SHA-256을 남기고, static profile 경로는 Git tree metadata만 기록한다. 어느 경로도
저장소 전체 archive나 실행 권한을 만들지 않는다. owner manifest의 `system_prompt` 본문은
profile/report/Raw Stream에 복제하지 않고 redacted marker로 대체한다.

`pack_selection`은 `WHY`, `EXPECT`, evidence refs, candidate domain,
canonical registry version과 pack candidate, `max_experiments=0`,
`fallback=terminal_compatibility_only`를 기록한다. 이 hand-off 자체는 fixture를 선택하거나
실험을 승인하지 않는다.

`compatibility_report`는 항상 `experiments_run=0`, `findings=[]`이며 다음 claim boundary를
포함한다.

> Compatibility only: repository code was not executed, synthetic failures were not
> attributed to this target, and no vulnerability or safety certification was produced.

Owner manifest가 있으면 `target_profile → pack_selection`으로 provenance와 compatibility
candidate를 남긴 뒤, #57의 별도 `behavior_profile → pack.selected` 라우터가 실제 실행 가능
여부를 다시 판정한다. 실행 가능한 Life pack만 `experiment_plan → synthetic diagnosis`로
이어진다. `compatibility_report`는 실험 없이 종료하는 static-only 경로에서만 terminal
report로 발행한다.

## 안전한 종료

| 조건 | 결과 |
|---|---|
| branch가 새 SHA로 이동 | 이전 static profile 미사용, `pinned_profile_not_registered` |
| deleted/empty/unresolvable repository | source resolution 실패, target profile/finding 생성 안 함 |
| allowlisted path 없음 | `static_evidence_missing`, experiments 0 |
| blob SHA/size 변경 | `static_evidence_drift`, experiments 0 |
| oversized/symlink/submodule/path traversal/secret-like path | policy reject, experiments 0 |
| owner manifest malformed | static profile로 덮지 않고 manifest 오류로 종료 |
| domain evidence 부족 | `unsupported`, 임의 attack/finding 없음 |

오류 원문에는 provider 응답이나 credential이 포함될 수 있으므로 event에는 안정적인
reason code만 남긴다.

## 검증

```bash
uv run pytest tests/unit/test_participant_intake.py -q
uv run pytest tests/integration/test_api.py::test_manifestless_participant_terminates_with_ordered_compatibility_report -q
node web/tests/core.test.mjs
```

Eval entry는 `tests/evals/cases.yaml`의 `participant-*` 세 case다.
