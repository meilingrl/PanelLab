"""数据库管理：库/用户列表、创建、删除（阶段 4）。"""
from __future__ import annotations

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

from fastapi import APIRouter, Depends, HTTPException

from models.user import User
from routers.auth import get_current_user
from schemas.db_admin import (
    DatabaseCreate,
    DatabaseItem,
    DatabaseListResponse,
    UserCreate,
    UserItem,
    UserListResponse,
)
from services.mysql_admin import (
    MysqlAdminError,
    create_database,
    create_user,
    delete_database,
    drop_user,
    list_databases,
    list_users,
)

router = APIRouter(prefix="/api/db", tags=["db-admin"])


def _handle_mysql_error(exc: MysqlAdminError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/databases", response_model=DatabaseListResponse)
def get_databases(_user: Annotated[User, Depends(get_current_user)]):
    """返回数据库列表（排除系统库与当前应用库）。"""
    try:
        rows = list_databases()
        return DatabaseListResponse(
            items=[DatabaseItem(**r) for r in rows],
        )
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"获取数据库列表失败: {str(e)}")


@router.post("/databases", response_model=DatabaseItem)
def post_database(
    body: DatabaseCreate,
    _user: Annotated[User, Depends(get_current_user)],
):
    """创建数据库。"""
    try:
        create_database(name=body.name, charset=body.charset, collation=body.collation)
        rows = [r for r in list_databases() if r["name"] == body.name]
        if rows:
            return DatabaseItem(**rows[0])
        return DatabaseItem(name=body.name, charset=body.charset, collation=body.collation)
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建数据库失败: {str(e)}")


@router.delete("/databases/{name}")
def delete_database_by_name(
    name: str,
    _user: Annotated[User, Depends(get_current_user)],
):
    """删除数据库。"""
    try:
        delete_database(name)
        return {"message": "数据库已删除"}
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除数据库失败: {str(e)}")


@router.get("/users", response_model=UserListResponse)
def get_users(_user: Annotated[User, Depends(get_current_user)]):
    """返回用户列表。"""
    try:
        rows = list_users()
        return UserListResponse(items=[UserItem(**r) for r in rows])
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"获取用户列表失败: {str(e)}")


@router.post("/users", response_model=UserItem)
def post_user(
    body: UserCreate,
    _user: Annotated[User, Depends(get_current_user)],
):
    """创建用户并授权。"""
    try:
        create_user(
            username=body.username,
            password=body.password,
            host=body.host,
            database=body.database,
            privileges=body.privileges,
        )
        return UserItem(user=body.username, host=body.host, grants_summary=None)
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建用户失败: {str(e)}")


@router.delete("/users/{username}")
def delete_user_by_name(
    username: str,
    _user: Annotated[User, Depends(get_current_user)],
    host: str = "%",
):
    """删除用户。可通过 query 指定 host。"""
    try:
        drop_user(username=username, host=host)
        return {"message": "用户已删除"}
    except MysqlAdminError as e:
        _handle_mysql_error(e)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除用户失败: {str(e)}")


# ---------- 计划任务预留（Plan 3.3）----------
@router.get("/jobs")
def get_jobs(_user: Annotated[User, Depends(get_current_user)]):
    """计划任务列表（预留，返回空）。"""
    return {"items": []}


@router.post("/jobs")
def post_job(_user: Annotated[User, Depends(get_current_user)]):
    """新建计划任务（预留）。"""
    raise HTTPException(status_code=501, detail="计划任务功能尚未实现")


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: int,
    _user: Annotated[User, Depends(get_current_user)],
):
    """删除计划任务（预留）。"""
    raise HTTPException(status_code=501, detail="计划任务功能尚未实现")
