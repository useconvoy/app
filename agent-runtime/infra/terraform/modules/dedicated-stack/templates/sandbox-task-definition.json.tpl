[
  {
    "name": "sandbox",
    "image": "${image}",
    "essential": true,
    "user": "1000:1000",
    "environment": [
      { "name": "CONVOY_STACK", "value": "${stack_name}" },
      { "name": "CONVOY_SANDBOX", "value": "true" }
    ],
    "linuxParameters": {
      "initProcessEnabled": true
    },
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "${log_group}",
        "awslogs-region": "${region}",
        "awslogs-stream-prefix": "sandbox"
      }
    }
  }
]
