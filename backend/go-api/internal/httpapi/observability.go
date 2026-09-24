package httpapi

import (
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
	"rag-mvp/backend/go-api/internal/observability"
)

func observabilityError(c *gin.Context, err error) {
	if errors.Is(err, observability.ErrInvalidInput) {
		fail(c, http.StatusBadRequest, "INVALID_OBSERVABILITY_FILTER", "观测筛选条件无效。")
		return
	}
	fail(c, http.StatusServiceUnavailable, "OBSERVABILITY_UNAVAILABLE", "观测服务暂不可用。")
}

func (s *Server) observabilityMetrics(c *gin.Context) {
	window := c.Query("window")
	if !observability.ValidWindow(window) {
		observabilityError(c, observability.ErrInvalidInput)
		return
	}
	if s.Observability == nil {
		observabilityError(c, observability.ErrUnavailable)
		return
	}
	result, err := s.Observability.Metrics(c.Request.Context(), window)
	if err != nil {
		observabilityError(c, err)
		return
	}
	c.JSON(http.StatusOK, result)
}

func (s *Server) observabilityTraces(c *gin.Context) {
	service, window := c.Query("service"), c.Query("window")
	if !observability.ValidService(service) || !observability.ValidWindow(window) {
		observabilityError(c, observability.ErrInvalidInput)
		return
	}
	if s.Observability == nil {
		observabilityError(c, observability.ErrUnavailable)
		return
	}
	result, err := s.Observability.Traces(c.Request.Context(), service, window)
	if err != nil {
		observabilityError(c, err)
		return
	}
	c.JSON(http.StatusOK, result)
}

func (s *Server) observabilityTrace(c *gin.Context) {
	traceID := c.Param("trace_id")
	if !observability.ValidTraceID(traceID) {
		observabilityError(c, observability.ErrInvalidInput)
		return
	}
	if s.Observability == nil {
		observabilityError(c, observability.ErrUnavailable)
		return
	}
	result, err := s.Observability.Trace(c.Request.Context(), traceID)
	if err != nil {
		observabilityError(c, err)
		return
	}
	c.JSON(http.StatusOK, result)
}
