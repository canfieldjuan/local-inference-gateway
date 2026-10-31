# Email schema pins

These public schemas contain declarations only, with no email content or prompts.
They were generated with the gateway's locked Pydantic dependency by evaluating
only the pinned `Analysis` class and, where present, `analysis_response_schema`
function from `eom-email-watcher/src/eom_email_watcher/model.py`.

| Fixture | App revision | Source file SHA256 |
| --- | --- | --- |
| email_analysis_main.json | `69b98e958eebb7f5941831b9c27373aa8d4b3fbf` | `00eed84c552b79c7ca168bdc32cf15f71c9b10ac5aa1636f245417c2f16bca38` |
| email_analysis_pr314.json | `7338985b2a7501a3a4e3513457396601a8dd4b23` | `45925bd5ecec523b7a74b295e7aa3b3084023b9a0b10e95d4c14a53b2e782ca8` |

The main fixture uses `Analysis.model_json_schema()`. The PR314 fixture uses
`analysis_response_schema()`, including its decoder-only date length bounds.
Do not refresh these from a moving branch: they are the consumer shapes in the
accepted email decoder-order contract, not a claim about a current installation.
