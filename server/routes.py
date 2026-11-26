"""集中注册工作台、认证与用户管理路由。"""

from fastapi import APIRouter, FastAPI

from server.features_services import login_and_register, model_settings, output_settings, project_service, roommate, user_service
from server.solutions import router as solutions_router


def register_routes(app: FastAPI, workbench_router: APIRouter) -> None:
    app.include_router(login_and_register.router)
    app.include_router(user_service.router)
    app.include_router(project_service.router)
    app.include_router(model_settings.router)
    app.include_router(output_settings.router)
    app.include_router(workbench_router)
    app.include_router(solutions_router)
    app.include_router(roommate.router)
