package httpapi

import (
	"context"
	"crypto/subtle"
	"database/sql"
	"errors"
	"github.com/gin-gonic/gin"
	"net/http"
	"net/mail"
	"rag-mvp/backend/go-api/internal/ragclient"
	"rag-mvp/backend/go-api/internal/security"
	"rag-mvp/backend/go-api/internal/storage"
	"strings"
	"sync"
	"time"
)

type Server struct {
	Store              *storage.Store
	RAG                *ragclient.Client
	Vault              *security.Vault
	JWTKey             []byte
	Origin             string
	Secure             bool
	EmbeddingModel     string
	EmbeddingDimension uint32
	AllowLocalModels   bool
	authSlots          chan struct{}
	mu                 sync.Mutex
	runs               map[string]bool
}

func fail(c *gin.Context, status int, code, message string) {
	c.AbortWithStatusJSON(status, gin.H{"code": code, "message": message})
}
func uid(c *gin.Context) string { return c.GetString("user") }
func (s *Server) Router() *gin.Engine {
	s.authSlots = make(chan struct{}, 4)
	s.runs = map[string]bool{}
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(func(c *gin.Context) {
		c.Writer.Header().Set("X-Content-Type-Options", "nosniff")
		c.Writer.Header().Set("Cache-Control", "no-store")
		c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 33<<20)
		if c.Request.Method != "GET" && c.Request.Method != "HEAD" {
			origin := c.GetHeader("Origin")
			if origin != "" && origin != s.Origin {
				fail(c, 403, "ORIGIN_REJECTED", "请求来源不合法。")
				return
			}
		}
		c.Next()
	})
	r.GET("/healthz", func(c *gin.Context) { c.JSON(200, gin.H{"status": "ok"}) })
	r.GET("/readyz", func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
		defer cancel()
		if e := s.Store.DB.PingContext(ctx); e != nil {
			fail(c, 503, "NOT_READY", "数据库不可用。")
			return
		}
		c.JSON(200, gin.H{"status": "ok"})
	})
	r.POST("/auth/register", s.register)
	r.POST("/auth/login", s.login)
	a := r.Group("", s.authenticate)
	a.GET("/me", func(c *gin.Context) {
		u, e := s.Store.UserID(c.Request.Context(), uid(c))
		if e != nil {
			fail(c, 401, "AUTH_EXPIRED", "请重新登录。")
			return
		}
		c.JSON(200, u)
	})
	a.POST("/auth/logout", s.logout)
	a.GET("/settings", s.settings)
	a.PUT("/settings/models/:kind", s.saveModel)
	a.PUT("/settings/agent", func(c *gin.Context) {
		var p struct {
			Enabled bool `json:"rerankEnabled"`
		}
		if c.ShouldBindJSON(&p) != nil {
			fail(c, 400, "INVALID_INPUT", "参数无效。")
			return
		}
		if p.Enabled {
			fail(c, 409, "RERANK_UNAVAILABLE", "当前 Python 部署尚未接入用户级 Rerank。")
			return
		}
		s.settings(c)
	})
	a.GET("/datasets", s.datasets)
	a.POST("/datasets", s.createDataset)
	a.GET("/datasets/:id", s.dataset)
	a.POST("/datasets/:id/documents", s.upload)
	a.GET("/datasets/:id/jobs", s.jobs)
	a.POST("/jobs/:id/:action", s.jobAction)
	a.DELETE("/documents/:id", s.deleteDocument)
	a.POST("/chat/stream", s.chat)
	a.GET("/conversations", s.conversations)
	a.GET("/conversations/:id/messages", s.messages)
	return r
}
func (s *Server) authenticate(c *gin.Context) {
	token, e := c.Cookie("rag_token")
	if e != nil {
		fail(c, 401, "AUTH_EXPIRED", "登录已过期，请重新登录。")
		return
	}
	cl, e := security.Parse(s.JWTKey, token)
	if e != nil {
		fail(c, 401, "AUTH_EXPIRED", "登录已过期，请重新登录。")
		return
	}
	revoked, e := s.Store.Revoked(c.Request.Context(), cl.ID)
	if e != nil {
		fail(c, 503, "DATABASE_UNAVAILABLE", "认证服务暂不可用。")
		return
	}
	if revoked {
		fail(c, 401, "AUTH_EXPIRED", "请重新登录。")
		return
	}
	if c.Request.Method != "GET" && subtle.ConstantTimeCompare([]byte(c.GetHeader("X-CSRF-Token")), []byte(cl.CSRF)) != 1 {
		fail(c, 403, "CSRF_REJECTED", "请刷新页面后重试。")
		return
	}
	c.Set("user", cl.Subject)
	c.Set("claims", cl)
	c.Next()
}
func (s *Server) credentials(c *gin.Context) (string, string, bool) {
	var p struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if c.ShouldBindJSON(&p) != nil {
		fail(c, 400, "INVALID_INPUT", "请输入邮箱和密码。")
		return "", "", false
	}
	p.Email = strings.ToLower(strings.TrimSpace(p.Email))
	m, e := mail.ParseAddress(p.Email)
	if e != nil || m.Address != p.Email || len(p.Email) > 254 || len(p.Password) < 8 || len(p.Password) > 128 {
		fail(c, 400, "INVALID_INPUT", "请输入有效邮箱和 8–128 位密码。")
		return "", "", false
	}
	select {
	case s.authSlots <- struct{}{}:
		return p.Email, p.Password, true
	default:
		fail(c, 429, "BUSY", "请稍后再试。")
		return "", "", false
	}
}
func (s *Server) session(c *gin.Context, u storage.User) {
	token, claims, e := security.Sign(s.JWTKey, u.ID)
	if e != nil {
		fail(c, 500, "AUTH_FAILED", "登录失败。")
		return
	}
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("rag_token", token, 86400, "/", "", s.Secure, true)
	c.SetCookie("rag_csrf", claims.CSRF, 86400, "/", "", s.Secure, false)
	c.JSON(200, u)
}
func (s *Server) register(c *gin.Context) {
	email, password, ok := s.credentials(c)
	if !ok {
		return
	}
	defer func() { <-s.authSlots }()
	u := storage.User{ID: security.ID(), Email: email, Language: "zh-CN", Hash: security.Hash(password)}
	if e := s.Store.CreateUser(c.Request.Context(), u); e != nil {
		fail(c, 409, "REGISTER_FAILED", "无法注册此邮箱。")
		return
	}
	s.session(c, u)
}
func (s *Server) login(c *gin.Context) {
	email, password, ok := s.credentials(c)
	if !ok {
		return
	}
	defer func() { <-s.authSlots }()
	u, e := s.Store.User(c.Request.Context(), email)
	if e != nil && !errors.Is(e, sql.ErrNoRows) {
		fail(c, 503, "DATABASE_UNAVAILABLE", "登录服务暂不可用。")
		return
	}
	if e != nil {
		security.Hash(password)
		fail(c, 401, "INVALID_CREDENTIALS", "邮箱或密码错误。")
		return
	}
	if !security.Verify(password, u.Hash) {
		fail(c, 401, "INVALID_CREDENTIALS", "邮箱或密码错误。")
		return
	}
	s.session(c, u)
}
func (s *Server) logout(c *gin.Context) {
	v, _ := c.Get("claims")
	cl := v.(*security.Claims)
	if e := s.Store.Revoke(c.Request.Context(), cl.ID, cl.ExpiresAt.Time); e != nil {
		fail(c, 503, "LOGOUT_FAILED", "退出失败，请重试。")
		return
	}
	c.SetCookie("rag_token", "", -1, "/", "", s.Secure, true)
	c.SetCookie("rag_csrf", "", -1, "/", "", s.Secure, false)
	c.Status(204)
}
func (s *Server) owned(c *gin.Context, id, kind string) (storage.Resource, bool) {
	r, e := s.Store.Resource(c.Request.Context(), uid(c), id)
	if e != nil || r.Kind != kind {
		fail(c, 404, "NOT_FOUND", "资源不存在。")
		return r, false
	}
	return r, true
}
func key(c *gin.Context) string {
	k := c.GetHeader("Idempotency-Key")
	if k == "" || len(k) > 128 {
		k = security.ID()
	}
	return k
}
