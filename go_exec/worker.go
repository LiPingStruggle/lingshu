package main

import (
	"encoding/json"
	"log"
	"net/http"
)

type TaskRequest struct {
	TaskID      string `json:"task_id"`
	Description string `json:"description"`
	Status      string `json:"status,omitempty"`
	Result      string `json:"result,omitempty"`
}

func executeTask(w http.ResponseWriter, r *http.Request) {
	var req TaskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	req.Status = "done"
	req.Result = "Executed by Go worker: " + req.Description

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(req)

	log.Printf("Executed task %s: %s", req.TaskID, req.Description)
}

func healthCheck(w http.ResponseWriter, r *http.Request) {
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func main() {
	http.HandleFunc("/execute_task", executeTask)
	http.HandleFunc("/health", healthCheck)
	log.Println("Go worker listening on :9000")
	log.Fatal(http.ListenAndServe(":9000", nil))
}