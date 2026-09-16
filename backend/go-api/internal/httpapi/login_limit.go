package httpapi

import (
	"strings"
	"sync"
	"time"
)

// loginLimiter 按「客户端地址 + 邮箱」限制失败登录，抵御在线暴力尝试。
//
// 生产实测：12 次连续错误密码全部返回 401，没有任何限流或退避幅度；
// 结合允许 8 位纯数字口令的策略，公网入口可被持续尝试。这里只做内存级、
// 有界的失败计数：超过窗口内的失败次数先拒绝一段时间，成功登录即清零。
// 计数不落库（避免为攻击者写数据），进程重启后失效是可接受的代价。
type loginLimiter struct {
	mu       sync.Mutex
	failures map[string][]time.Time
	limit    int
	window   time.Duration
	blocked  time.Duration
}

func newLoginLimiter(limit int, window, blocked time.Duration) *loginLimiter {
	return &loginLimiter{failures: map[string][]time.Time{}, limit: limit, window: window, blocked: blocked}
}

// retryAfter 返回还需等待多久；0 表示当前允许尝试。
func (l *loginLimiter) retryAfter(key string, now time.Time) time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()
	kept := l.prune(key, now)
	if len(kept) < l.limit {
		return 0
	}
	// 从最早的一次失败起算封锁窗口，保证攻击者无法靠持续尝试无限延长期限。
	until := kept[0].Add(l.blocked)
	if until.Before(now) {
		return 0
	}
	return until.Sub(now)
}

// fail 记录一次失败尝试。
func (l *loginLimiter) fail(key string, now time.Time) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.failures[key] = append(l.prune(key, now), now)
}

// reset 在成功登录后清零该键的失败记录。
func (l *loginLimiter) reset(key string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.failures, key)
}

// prune 丢弃窗口外的失败记录；调用方必须持有锁。
func (l *loginLimiter) prune(key string, now time.Time) []time.Time {
	kept := l.failures[key][:0]
	for _, at := range l.failures[key] {
		if now.Sub(at) <= l.window {
			kept = append(kept, at)
		}
	}
	if len(kept) == 0 {
		delete(l.failures, key)
		return nil
	}
	l.failures[key] = kept
	return kept
}

// loginKey 组合客户端地址与账号：同一地址下的不同账号互不影响，
// 同一账号从多个地址尝试仍会被逐地址限制。
func loginKey(clientIP, email string) string {
	return clientIP + "|" + strings.ToLower(strings.TrimSpace(email))
}

// limiter 惰性构造进程内限流器：Server 可能是测试里的字面量，不要求显式初始化。
// 两级限流：按「地址 + 账号」10 次失败即封锁，并按「地址」30 次失败封锁，
// 后者用于拦截轮换邮箱的尝试（只按账号限流会被轻易绕过）。
func (s *Server) limiter() *loginLimiter {
	s.limitOnce.Do(func() {
		s.logins = newLoginLimiter(10, 5*time.Minute, 15*time.Minute)
		s.loginsByIP = newLoginLimiter(30, 5*time.Minute, 15*time.Minute)
	})
	return s.logins
}

// ipLimiter 返回按客户端地址维度的限流器。
func (s *Server) ipLimiter() *loginLimiter {
	s.limiter()
	return s.loginsByIP
}
