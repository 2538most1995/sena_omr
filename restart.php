<?php
header('Content-Type: text/plain; charset=utf-8');

$base = __DIR__ . '/omr-mobile-scanner';
$stopScript = $base . '/scripts/plesk-stop.sh';
$startScript = $base . '/scripts/plesk-start.sh';
$initScript = $base . '/scripts/init-mysql.py';
$python = $base . '/.venv/bin/python';

echo "=== OMR Service Restart Manager ===\n\n";

// Optional: initialize MySQL tables if ?init_db=1
if (isset($_GET['init_db'])) {
    echo "--- Initialising MySQL Tables ---\n";
    if (file_exists($python) && file_exists($initScript)) {
        echo shell_exec(escapeshellcmd($python) . " " . escapeshellarg($initScript) . " --tables-only 2>&1") . "\n";
    } else {
        echo "Python or init-mysql script not found.\n";
    }
    echo "\n";
}

echo "1. Stopping existing OMR process...\n";
if (file_exists($stopScript)) {
    echo shell_exec("bash " . escapeshellarg($stopScript) . " 2>&1") . "\n";
} else {
    echo "Stop script not found: {$stopScript}\n";
}

sleep(1);

echo "2. Starting OMR service with new code...\n";
if (file_exists($startScript)) {
    echo shell_exec("bash " . escapeshellarg($startScript) . " 2>&1") . "\n";
} else {
    echo "Start script not found: {$startScript}\n";
}

echo "\n3. Checking /api/health...\n";
$ch = curl_init("http://127.0.0.1:18080/api/health");
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 5);
$response = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

echo "HTTP Code: {$code}\n";
echo "Response: " . ($response ?: 'No response') . "\n";
echo "\n=== Done. You can now reload https://krumost.com/sena_omr/ ===\n";
