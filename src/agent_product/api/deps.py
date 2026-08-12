from fastapi import Request
from pydantic_ai import Agent


def get_agent(request: Request) -> Agent:
    return request.app.state.agent

