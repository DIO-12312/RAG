package httpapi

import (
	"testing"
	"time"
)

func TestLoginLimiterBlocksAfterRepeatedFailures(t *testing.T) {
	limiter := newLoginLimiter(3, 5*time.Minute, 15*time.Minute)
	now := time.Now()
	key := loginKey("203.0.113.7", "User@Example.test")

	for i := 0; i < 3; i++ {
		if wait := limiter.retryAfter(key, now); wait != 0 {
			t.Fatalf("第 %d 次尝试不应被拦截，实际等待 %s", i+1, wait)
		}
		limiter.fail(key, now)
	}
	if wait := limiter.retryAfter(key, now); wait <= 0 {
		t.Fatal("达到失败上限后必须拒绝")
	}
	// 窗口之外的旧失败不应继续累计。
	later := now.Add(6 * time.Minute)
	if wait := limiter.retryAfter(key, later); wait != 0 {
		t.Fatalf("窗口过期后应重新允许，实际等待 %s", wait)
	}
	// 成功登录后清零。
	limiter.fail(key, later)
	limiter.reset(key)
	if wait := limiter.retryAfter(key, later); wait != 0 {
		t.Fatalf("成功登录后必须清零，实际等待 %s", wait)
	}
}

func TestLoginKeyIsolatesAccountAndClient(t *testing.T) {
	base := loginKey("203.0.113.7", "a@example.test")
	if base == loginKey("203.0.113.7", "b@example.test") {
		t.Fatal("不同账号必须使用不同限流键")
	}
	if base == loginKey("203.0.113.8", "a@example.test") {
		t.Fatal("不同客户端地址必须使用不同限流键")
	}
	if base != loginKey("203.0.113.7", " A@Example.Test ") {
		t.Fatal("同一账号的大小写与空白差异必须归一为同一键")
	}
}
