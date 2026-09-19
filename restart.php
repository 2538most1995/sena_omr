<?php
header('Content-Type: text/plain; charset=utf-8');

$base = __DIR__ . '/omr-mobile-scanner';
echo "=== Diagnosing Python on krumost.com ===\n\n";

echo "1. Directory listing of omr-mobile-scanner:\n";
echo shell_exec("ls -la " . escapeshellarg($base) . " 2>&1") . "\n";

echo "2. Directory listing of .venv/bin:\n";
echo shell_exec("ls -la " . escapeshellarg($base . '/.venv/bin') . " 2>&1") . "\n";

echo "3. Testing .venv/bin/python directly:\n";
echo shell_exec(escapeshellarg($base . '/.venv/bin/python') . " -V 2>&1") . "\n";

echo "4. Testing system python:\n";
echo shell_exec("which python3 python 2>&1") . "\n";

echo "5. Testing uv in .tools:\n";
echo shell_exec("ls -la " . escapeshellarg($base . '/.tools') . " 2>&1") . "\n";

if (isset($_GET['bootstrap'])) {
    echo "\n=== Running plesk-bootstrap.sh ===\n";
    echo shell_exec("bash " . escapeshellarg($base . '/scripts/plesk-bootstrap.sh') . " 2>&1") . "\n";
    echo "\n=== Running plesk-start.sh ===\n";
    echo shell_exec("bash " . escapeshellarg($base . '/scripts/plesk-start.sh') . " 2>&1") . "\n";
}
