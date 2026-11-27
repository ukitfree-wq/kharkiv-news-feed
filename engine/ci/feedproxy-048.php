<?php
/*
 * feedproxy-048.php — захищений проксі для новинного фіду (джерело 048.ua)
 * =========================================================================
 *
 * ЩО ЦЕ РОБИТЬ
 *   Сайт 048.ua блокує сервери GitHub (відповідає 403), тому збирач новин
 *   (GitHub Actions) завантажує сторінки 048.ua через цей файл на вашому
 *   хостингу. Скрипт бере ТІЛЬКИ адреси https://www.048.ua/… та https://048.ua/…
 *   і віддає їх як є (код відповіді й тип вмісту). Будь-які інші сайти він
 *   відхиляє — це НЕ відкритий проксі.
 *
 *   Без секретного ключа (заголовок X-Proxy-Key) скрипт завжди відповідає
 *   403 Forbidden. Тому, якщо відкрити його в браузері й побачити
 *   «Forbidden» — це нормально: файл працює.
 *
 * КУДИ ЗАВАНТАЖИТИ
 *   У корінь сайту (зазвичай папка public_html, www або htdocs — там, де лежать
 *   wp-config.php і wp-login.php) через файловий менеджер хостингу або FTP.
 *   Назву файлу можна залишити feedproxy-048.php.
 *   Перевірка: відкрийте https://ВАШ-ДОМЕН/feedproxy-048.php — має бути 403 «Forbidden».
 *
 * ВИМОГИ: PHP 7.4–8.x; бажано розширення curl (якщо його нема — працює через
 *   file_get_contents). Нічого не логуються, ключ ніде не записується.
 *   Відповіді кешуються на 60 секунд у тимчасовій папці сервера, щоб не
 *   навантажувати 048.ua.
 *
 * КЛЮЧ: константа PROXY_KEY нижче. Не змінюйте її і нікому не пересилайте файл.
 */

const PROXY_KEY = '__PUT_KEY_HERE__';

const ALLOWED_HOSTS = array('www.048.ua', '048.ua');
const MAX_BYTES = 8388608;          // 8 MB (the 048.ua RSS alone is ~3 MB)
const CONNECT_TIMEOUT = 10;         // seconds
const TOTAL_TIMEOUT = 25;           // seconds
const MAX_REDIRECTS = 5;
const CACHE_TTL = 60;               // seconds
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

function fp_deny($code, $msg)
{
    http_response_code($code);
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: private, no-store, max-age=0');
    header('X-LiteSpeed-Cache-Control: no-cache');
    header('X-Robots-Tag: noindex, nofollow');
    echo $msg . "\n";
    exit;
}

function fp_allowed($url)
{
    if (!is_string($url) || $url === '' || strlen($url) > 2048) {
        return false;
    }
    if (preg_match('/[\x00-\x20\x7f\\\\]/', $url)) {
        return false;
    }
    $p = parse_url($url);
    if (!is_array($p) || !isset($p['scheme'], $p['host'])) {
        return false;
    }
    if (strtolower($p['scheme']) !== 'https') {
        return false;
    }
    if (isset($p['user']) || isset($p['pass'])) {
        return false;
    }
    if (isset($p['port']) && (int)$p['port'] !== 443) {
        return false;
    }
    return in_array(strtolower($p['host']), ALLOWED_HOSTS, true);
}

function fp_resolve($base, $location)
{
    if (preg_match('#^https?://#i', $location)) {
        return $location;
    }
    $b = parse_url($base);
    $origin = $b['scheme'] . '://' . $b['host'];
    if (strpos($location, '//') === 0) {
        return $b['scheme'] . ':' . $location;
    }
    if (strpos($location, '/') === 0) {
        return $origin . $location;
    }
    $path = isset($b['path']) ? preg_replace('#/[^/]*$#', '/', $b['path']) : '/';
    return $origin . $path . $location;
}

function fp_request_headers()
{
    $h = array(
        'User-Agent: ' . USER_AGENT,
        'Accept: ' . (isset($_SERVER['HTTP_ACCEPT']) ? substr($_SERVER['HTTP_ACCEPT'], 0, 300) : '*/*'),
        'Accept-Language: uk-UA,uk;q=0.9,en;q=0.5',
    );
    if (!empty($_SERVER['HTTP_IF_NONE_MATCH'])) {
        $h[] = 'If-None-Match: ' . substr($_SERVER['HTTP_IF_NONE_MATCH'], 0, 300);
    }
    if (!empty($_SERVER['HTTP_IF_MODIFIED_SINCE'])) {
        $h[] = 'If-Modified-Since: ' . substr($_SERVER['HTTP_IF_MODIFIED_SINCE'], 0, 100);
    }
    return $h;
}

/* One upstream request without following redirects.
 * Returns array(status, headers[lowercase name => value], body) or null on network error. */
function fp_fetch_once($url)
{
    if (function_exists('curl_init')) {
        $hdrs = array();
        $body = '';
        $tooBig = false;
        $ch = curl_init($url);
        curl_setopt_array($ch, array(
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_RETURNTRANSFER => false,
            CURLOPT_CONNECTTIMEOUT => CONNECT_TIMEOUT,
            CURLOPT_TIMEOUT => TOTAL_TIMEOUT,
            CURLOPT_HTTPHEADER => fp_request_headers(),
            CURLOPT_ENCODING => '',
            CURLOPT_SSL_VERIFYPEER => true,
            CURLOPT_SSL_VERIFYHOST => 2,
            CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$hdrs) {
                $parts = explode(':', $line, 2);
                if (count($parts) === 2) {
                    $hdrs[strtolower(trim($parts[0]))] = trim($parts[1]);
                }
                return strlen($line);
            },
            CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use (&$body, &$tooBig) {
                if (strlen($body) + strlen($chunk) > MAX_BYTES) {
                    $tooBig = true;
                    return 0; // abort transfer
                }
                $body .= $chunk;
                return strlen($chunk);
            },
        ));
        if (defined('CURLOPT_PROTOCOLS')) {
            curl_setopt($ch, CURLOPT_PROTOCOLS, CURLPROTO_HTTPS);
        }
        $ok = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($tooBig) {
            fp_deny(502, 'Upstream response too large');
        }
        if ($ok === false || $status === 0) {
            return null;
        }
        return array($status, $hdrs, $body);
    }

    // Fallback without the curl extension.
    $ctx = stream_context_create(array(
        'http' => array(
            'method' => 'GET',
            'header' => implode("\r\n", fp_request_headers()),
            'follow_location' => 0,
            'max_redirects' => 1,
            'ignore_errors' => true,
            'timeout' => TOTAL_TIMEOUT,
            'protocol_version' => 1.1,
        ),
        'ssl' => array('verify_peer' => true, 'verify_peer_name' => true),
    ));
    $fp = @fopen($url, 'rb', false, $ctx);
    if ($fp === false) {
        return null;
    }
    $body = stream_get_contents($fp, MAX_BYTES + 1);
    $meta = stream_get_meta_data($fp);
    fclose($fp);
    if ($body === false) {
        return null;
    }
    if (strlen($body) > MAX_BYTES) {
        fp_deny(502, 'Upstream response too large');
    }
    $status = 0;
    $hdrs = array();
    $raw = isset($meta['wrapper_data']) && is_array($meta['wrapper_data']) ? $meta['wrapper_data'] : array();
    foreach ($raw as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
            $status = (int)$m[1];
            $hdrs = array();
            continue;
        }
        $parts = explode(':', $line, 2);
        if (count($parts) === 2) {
            $hdrs[strtolower(trim($parts[0]))] = trim($parts[1]);
        }
    }
    if ($status === 0) {
        return null;
    }
    if (isset($hdrs['content-encoding']) && stripos($hdrs['content-encoding'], 'gzip') !== false && function_exists('gzdecode')) {
        $dec = @gzdecode($body);
        if ($dec !== false) {
            $body = $dec;
        }
    }
    return array($status, $hdrs, $body);
}

// ---------------------------------------------------------------- main
if (PROXY_KEY === '' || strpos(PROXY_KEY, '__PUT_KEY') === 0) {
    fp_deny(403, 'Forbidden');
}
$given = isset($_SERVER['HTTP_X_PROXY_KEY']) ? (string)$_SERVER['HTTP_X_PROXY_KEY'] : '';
if ($given === '' || !hash_equals(PROXY_KEY, $given)) {
    fp_deny(403, 'Forbidden');
}
$method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
if ($method !== 'GET') {
    fp_deny(405, 'Method not allowed');
}
$url = isset($_GET['url']) ? $_GET['url'] : '';
if (!fp_allowed($url)) {
    fp_deny(403, 'Forbidden: URL not allowed');
}

$conditional = !empty($_SERVER['HTTP_IF_NONE_MATCH']) || !empty($_SERVER['HTTP_IF_MODIFIED_SINCE']);
$cacheDir = @sys_get_temp_dir();
$cacheFile = ($cacheDir && @is_writable($cacheDir))
    ? rtrim($cacheDir, '/\\') . DIRECTORY_SEPARATOR . 'feedproxy048_' . sha1($url) . '.cache'
    : '';

$result = null;
$cacheState = 'MISS';
if ($cacheFile !== '' && !$conditional && @is_file($cacheFile) && (time() - @filemtime($cacheFile)) < CACHE_TTL) {
    $cached = @unserialize((string)@file_get_contents($cacheFile), array('allowed_classes' => false));
    if (is_array($cached) && count($cached) === 4) {
        $result = $cached;
        $cacheState = 'HIT';
    }
}

if ($result === null) {
    $current = $url;
    $resp = null;
    for ($i = 0; $i <= MAX_REDIRECTS; $i++) {
        $resp = fp_fetch_once($current);
        if ($resp === null) {
            fp_deny(502, 'Upstream fetch failed');
        }
        $status = $resp[0];
        if ($status >= 300 && $status < 400 && $status !== 304 && isset($resp[1]['location'])) {
            $next = fp_resolve($current, $resp[1]['location']);
            if (!fp_allowed($next)) {
                fp_deny(502, 'Redirect outside the allowlist');
            }
            $current = $next;
            continue;
        }
        break;
    }
    if ($resp[0] >= 300 && $resp[0] < 400 && $resp[0] !== 304) {
        fp_deny(502, 'Too many redirects');
    }
    $result = array($resp[0], $resp[1], $resp[2], $current);
    if ($cacheFile !== '' && $resp[0] === 200) {
        $tmp = $cacheFile . '.' . getmypid() . '.tmp';
        if (@file_put_contents($tmp, serialize($result)) !== false) {
            @rename($tmp, $cacheFile);
        }
    }
}

list($status, $headers, $body, $finalUrl) = $result;
http_response_code($status);
header('Content-Type: ' . (isset($headers['content-type']) ? $headers['content-type'] : 'application/octet-stream'));
foreach (array('etag' => 'ETag', 'last-modified' => 'Last-Modified') as $k => $name) {
    if (isset($headers[$k])) {
        header($name . ': ' . $headers[$k]);
    }
}
header('X-Final-URL: ' . $finalUrl);
header('X-Proxy-Cache: ' . $cacheState);
header('Cache-Control: private, no-store, max-age=0');
header('X-LiteSpeed-Cache-Control: no-cache');
header('X-Robots-Tag: noindex, nofollow');
// Compress the reply (the RSS is ~3 MB uncompressed, ~0.9 MB gzipped).
$acceptEnc = isset($_SERVER['HTTP_ACCEPT_ENCODING']) ? $_SERVER['HTTP_ACCEPT_ENCODING'] : '';
if (strlen($body) > 1024 && stripos($acceptEnc, 'gzip') !== false && function_exists('gzencode')) {
    $gz = gzencode($body, 6);
    if ($gz !== false) {
        $body = $gz;
        header('Content-Encoding: gzip');
        header('Vary: Accept-Encoding');
    }
}
header('Content-Length: ' . strlen($body));
echo $body;
