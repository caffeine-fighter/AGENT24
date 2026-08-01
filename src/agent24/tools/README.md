# Tool boundary

현재 synthetic gym의 Agents SDK function tool은 `inspect_synthetic_gym(query: str) -> str`다.

- 입력: 실패 상황을 설명하는 한 문장(최대 240자)
- 출력: `gym`, `scenario_id`, `failure_signal`, `probe`, reversible state를 담은 deterministic JSON
- timeout: 5초, `error_as_result`
- 외부 서비스/side effect: 없음. timeout·API 장애 시 adapter가 같은 shape의 offline 결과를 사용
- 비용·권한·개인정보: 로컬 synthetic 데이터만 사용하며 실제 계정·도구에 접근하지 않음

새 도구 하나당 아래 계약도 남깁니다.

- 입력 스키마와 예시
- 출력 스키마와 예시
- timeout과 최대 재시도 횟수
- 외부 서비스 장애 시 fallback
- 비용·권한·개인정보 영향

도구 이름은 모델이 선택 이유를 이해할 수 있도록 동사형으로 구체적으로 작성합니다.
