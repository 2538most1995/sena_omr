<?php
/**
 * OMR Service Manager & Deployer for Plesk
 */
header('Content-Type: text/plain; charset=utf-8');
set_time_limit(180);

$deployRoot = __DIR__;
$appDir = $deployRoot . '/omr-mobile-scanner';
$scriptsDir = $appDir . '/scripts';
$venvPython = $appDir . '/.venv/bin/python';

echo "====================================================\n";
echo "   SENA OMR - Plesk Auto-Deployer & Service Manager  \n";
echo "====================================================\n\n";

// 1. Git pull / init
echo "--- 1. Syncing latest code from Git ---\n";
chdir($deployRoot);

$envFile = $appDir . '/.env';
$envBackup = $deployRoot . '/.env.omr.bak';
if (file_exists($envFile)) {
    @copy($envFile, $envBackup);
}

if (!is_dir($deployRoot . '/.git')) {
    echo "Initializing git repository on server...\n";
    shell_exec("git init 2>&1");
    shell_exec("git config user.name 'Sena Deployer' 2>&1");
    shell_exec("git config user.email 'deployer@krumost.com' 2>&1");
    shell_exec("git remote add origin https://github.com/2538most1995/sena_omr.git 2>&1");
}

$gitOutput = shell_exec("git fetch origin main 2>&1 && git reset --hard origin/main 2>&1");
echo ($gitOutput ?: "Git sync completed.") . "\n\n";

// Restore .env if needed
if (!file_exists($envFile) && file_exists($envBackup)) {
    @copy($envBackup, $envFile);
    echo "Restored .env from backup.\n";
}

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

// 4. Initialize MySQL tables
if (isset($_GET['init_db']) || isset($_GET['db']) || true) {
    echo "--- 3. Checking MySQL & OMR Tables ---\n";
    $initScript = $scriptsDir . '/init-mysql.py';
    if (file_exists($venvPython) && file_exists($initScript)) {
        echo shell_exec(escapeshellarg($venvPython) . " " . escapeshellarg($initScript) . " --tables-only 2>&1") . "\n";
    }
    echo "\n";
}

// 5. Force kill old/orphan processes and restart daemon
echo "--- 4. Restarting OMR API Daemon ---\n";
shell_exec("bash " . escapeshellarg($scriptsDir . '/plesk-stop.sh') . " 2>&1");
sleep(1);
$startOutput = shell_exec("bash " . escapeshellarg($scriptsDir . '/plesk-start.sh') . " --restart 2>&1");
echo $startOutput . "\n\n";

// 6. Test Health
echo "--- 5. Checking API Health ---\n";
$ch = curl_init("http://127.0.0.1:18080/api/health");
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 8);
$response = curl_exec($ch);
$httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

echo "HTTP Status Code : {$httpCode}\n";
echo "Response Body    : " . ($response ?: 'No response') . "\n\n";

if ($httpCode === 200) {
    echo ">>> สำเร็จ! ระบบ OMR ทำงานสมบูรณ์แล้ว <<<\n";
    echo "เปิดใช้งานได้ที่: https://" . ($_SERVER['HTTP_HOST'] ?? 'krumost.com') . "/sena_omr/\n";
} else {
    echo ">>> กำลังตรวจสอบบันทึก uvicorn.log <<<\n";
    $logFile = $appDir . '/.run/uvicorn.log';
    if (file_exists($logFile)) {
        $lines = file($logFile);
        echo implode('', array_slice($lines, -30));
    }
}
