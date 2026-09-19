<?php
/**
 * OMR Service Manager & Deployer for Plesk
 */
header('Content-Type: text/plain; charset=utf-8');
// Disable time limit for bootstrap if needed
set_time_limit(180);

$deployRoot = __DIR__;
$appDir = $deployRoot . '/omr-mobile-scanner';
$scriptsDir = $appDir . '/scripts';
$venvPython = $appDir . '/.venv/bin/python';

echo "====================================================\n";
echo "   SENA OMR - Plesk Auto-Deployer & Service Manager  \n";
echo "====================================================\n\n";

// 1. Git pull
echo "--- 1. Pulling latest code from Git ---\n";
chdir($deployRoot);
$gitOutput = shell_exec("git pull origin main 2>&1");
echo ($gitOutput ?: "No git output (or git not available).") . "\n\n";

// 2. Make scripts executable
foreach (glob($scriptsDir . '/*.sh') as $script) {
    @chmod($script, 0755);
}

// 3. Ensure runtime/bootstrap
echo "--- 2. Checking Python Runtime ---\n";
$pythonWorking = false;
if (file_exists($venvPython)) {
    $ver = trim((string)shell_exec(escapeshellarg($venvPython) . " -V 2>/dev/null"));
    if ($ver) {
        $pythonWorking = true;
        echo "Python runtime OK: {$ver}\n\n";
    }
}

if (!$pythonWorking) {
    echo "Python runtime is missing or non-functional. Running plesk-bootstrap.sh...\n";
    $bootstrapOutput = shell_exec("bash " . escapeshellarg($scriptsDir . '/plesk-bootstrap.sh') . " 2>&1");
    echo $bootstrapOutput . "\n\n";
}

// 4. Initialize MySQL tables if requested
if (isset($_GET['init_db']) || isset($_GET['db'])) {
    echo "--- Initialising MySQL Tables ---\n";
    $initScript = $scriptsDir . '/init-mysql.py';
    if (file_exists($venvPython) && file_exists($initScript)) {
        echo shell_exec(escapeshellarg($venvPython) . " " . escapeshellarg($initScript) . " --tables-only 2>&1") . "\n";
    }
    echo "\n";
}

// 5. Restart OMR daemon
echo "--- 3. Restarting OMR API Daemon ---\n";
$startOutput = shell_exec("bash " . escapeshellarg($scriptsDir . '/plesk-start.sh') . " --restart 2>&1");
echo $startOutput . "\n\n";

// 6. Test Health
echo "--- 4. Checking API Health ---\n";
$ch = curl_init("http://127.0.0.1:18080/api/health");
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 6);
$response = curl_exec($ch);
$httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

echo "HTTP Status Code : {$httpCode}\n";
echo "Response Body    : " . ($response ?: 'No response') . "\n\n";

if ($httpCode === 200) {
    echo ">>> สำเร็จ! ระบบ OMR ทำงานสมบูรณ์แล้ว <<<\n";
    echo "เปิดใช้งานได้ที่: https://" . ($_SERVER['HTTP_HOST'] ?? 'krumost.com') . "/sena_omr/\n";
} else {
    echo ">>> ข้อผิดพลาด: โปรเซสยังไม่ตอบสนอง กรุณาตรวจสอบ uvicorn.log <<<\n";
    $logFile = $appDir . '/.run/uvicorn.log';
    if (file_exists($logFile)) {
        echo "\nTail of uvicorn.log:\n";
        $lines = file($logFile);
        echo implode('', array_slice($lines, -30));
    }
}
