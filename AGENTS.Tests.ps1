$projectRoot = Split-Path -Parent $PSScriptRoot
$agentsPath = Join-Path $projectRoot 'AGENTS.md'
$expectedLines = @(
    '注意事项：'
    '每次改动完成后，都必须创建一个Git commit，以便后续追踪和回滚。'
    '每次改动后，都必须编写或更新相关测试，并在交互给用户前，确保所有测试和验证都全部通过。'
    '修改现有功能时保持其他功能完整；新增功能前检查相关接口、数据格式和工作流的兼容性，并用回归测试验证。'
    '后端代码按职责放入对应的包。新增或修改的用户提示、模型提示、日志和报错文案统一维护在所属包的 `CONSTANT.py`，业务模块从该文件引用。`data-operations` 和 `data_model` 不新增 `CONSTANT.py`。'
)
$actualLines = [System.IO.File]::ReadAllLines($agentsPath, [System.Text.Encoding]::UTF8)

if ($actualLines.Count -ne $expectedLines.Count -or (Compare-Object $expectedLines $actualLines -SyncWindow 0)) {
    throw 'AGENTS.md does not match the required repository instructions.'
}

Write-Output 'AGENTS.md instructions verified.'
