$key = Read-Host "Enter OPENAI_API_KEY" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
)

[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $plain, "User")
$env:OPENAI_API_KEY = $plain

Write-Host "OPENAI_API_KEY has been set for the current user and this session."
Write-Host "Optional: set OPENAI_BASE_URL for OpenAI-compatible providers, e.g."
Write-Host "  [Environment]::SetEnvironmentVariable('OPENAI_BASE_URL', 'https://api.deepseek.com', 'User')"
Write-Host "Then verify: python scripts\check_llm.py"
