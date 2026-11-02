"""One synchronous preview/claim/apply workflow for this panel's local Nginx."""
import difflib
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
import jwt
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from models.site_config import SiteConfig
from models.site_change import SiteChange
from schemas.sites import SiteBase, SiteItem
from schemas.site_changes import PreviewClaims, SiteChangeItem
from services.nginx_manager import NginxManager, NginxManagerError, SiteRuntimeConfig


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':'), default=str).encode()).hexdigest()


def database_hash(db):
    # Global runtime: a simulated or disabled change must also invalidate competing previews.
    return digest([{c.name: getattr(row,c.name) for c in SiteConfig.__table__.columns}
                   for row in db.query(SiteConfig).order_by(SiteConfig.id).all()])


def runtime_hash(manager):
    s=manager.settings
    return digest([str(manager.config_dir()), s.nginx_test_cmd, s.nginx_reload_cmd,
                   s.nginx_skip_apply, s.nginx_cmd_timeout_seconds])


def resolve(db, user_id, request, manager, filename=None):
    row = None
    if request.site_id is not None:
        row=db.query(SiteConfig).filter_by(id=request.site_id,user_id=user_id).first()
        if row is None: raise HTTPException(404, '站点不存在')
    config=request.config
    if config is None:
        try:
            config=SiteBase(**{name:(getattr(row,name) or '' if name in ('root_path','proxy_target') else getattr(row,name))
                               for name in SiteBase.model_fields})
        except ValidationError:
            raise HTTPException(422, '现有站点参数不符合安全约束，请先预览修改') from None
    if request.operation != 'delete':
        others=db.query(SiteConfig)
        if row: others=others.filter(SiteConfig.id != row.id)
        # Keep legacy per-owner domain uniqueness and add global domain+port collision guard.
        conflict=others.filter(((SiteConfig.user_id==user_id) &
                               ((SiteConfig.name==config.name) | (SiteConfig.domain==config.domain))) |
                              ((SiteConfig.domain==config.domain) & (SiteConfig.listen_port==config.listen_port))).first()
        if conflict: raise HTTPException(409,'站点名称或监听域名冲突')
    target = manager.get_config_filename(SiteRuntimeConfig(row.id,row.name,row.domain,row.site_type,row.root_path,row.proxy_target,row.listen_port,row.enabled,row.config_filename)) if row else (filename or f'site-{uuid.uuid4().hex}.conf')
    if filename is not None and target != filename: raise HTTPException(409,'目标已变化，请重新预览')
    before=manager.read_config(target)
    if not row and before is not None: raise HTTPException(409,'目标配置文件已存在，请重新预览')
    candidate=SiteRuntimeConfig(row.id if row else 0, **config.model_dump(), config_filename=target)
    after=manager.render_site_config(candidate) if request.operation != 'delete' and config.enabled else None
    return row,config,target,before,after


def preview(db,user,request):
    manager=NginxManager()
    try:
        directory_hash=manager.fingerprint()
        db_hash=database_hash(db)
        row,config,filename,before,after=resolve(db,user.id,request,manager)
        if manager.fingerprint()!=directory_hash:
            raise HTTPException(409,'配置目录正在变化，请重新预览')
        now=int(time.time())
        claims=PreviewClaims(**request.model_dump(), purpose='site-change-preview',aud='site-change',sub=str(user.id),
                             jti=uuid.uuid4().hex,iat=now,exp=now+300,database_hash=db_hash,
                             directory_hash=directory_hash,runtime_hash=runtime_hash(manager),filename=filename)
        token=jwt.encode(claims.model_dump(),manager.settings.secret_key,algorithm='HS256')
        return dict(token=token,expires_at=datetime.fromtimestamp(now+300,timezone.utc),operation=request.operation,
                    site_id=request.site_id,site_name=config.name,before=before,after=after,
                    diff=''.join(difflib.unified_diff((before or '').splitlines(keepends=True),(after or '').splitlines(keepends=True),fromfile=filename+' (before)',tofile=filename+' (after)')),
                    simulated=manager.settings.nginx_skip_apply,
                    warnings=['作用于面板本机；预览不运行 nginx -t。','reload 命令成功不证明 HTTP 可用或 worker 替换完成。','配置前后可能包含手写敏感信息，未自动脱敏。'] + (['模拟模式：不会写配置或运行 Nginx。'] if manager.settings.nginx_skip_apply else []))
    except NginxManagerError as exc:
        raise HTTPException(422,exc.detail) from None


def execute(db,user,token):
    manager=NginxManager()
    try:
        payload=jwt.decode(token,manager.settings.secret_key,algorithms=['HS256'],audience='site-change',options={'require':['exp','iat','sub','jti']})
        claims=PreviewClaims.model_validate(payload)
        if claims.exp-claims.iat != 300: raise ValueError()
    except (jwt.PyJWTError,ValidationError,ValueError):
        raise HTTPException(401,'预览凭证无效或已过期，请重新预览') from None
    if claims.sub != str(user.id): raise HTTPException(403,'预览凭证不属于当前用户')
    user_id=user.id
    # End the authentication read transaction so the lock observes committed state.
    db.rollback()
    try:
        with manager.change_lock():
            if time.time() >= claims.exp:
                raise HTTPException(401, '预览凭证已过期，请重新预览')
            db.expire_all()
            if db.query(SiteChange).filter_by(jti=claims.jti).first():
                raise HTTPException(409,'此预览已执行，请查看历史或重新预览')
            if (database_hash(db)!=claims.database_hash or manager.fingerprint()!=claims.directory_hash
                    or runtime_hash(manager)!=claims.runtime_hash):
                raise HTTPException(409,'站点、配置或运行设置已变化，请重新预览')
            row,config,filename,before,after=resolve(db,user_id,claims,manager,claims.filename)
            change=SiteChange(jti=claims.jti,user_id=user_id,site_id=claims.site_id,site_name=config.name,
                              operation=claims.operation,status='pending',before=before,after=after,steps=[],
                              message='变更执行中；若此状态持续，请人工核查配置和运行态，勿盲目重试',simulated=manager.settings.nginx_skip_apply)
            db.add(change)
            try:
                db.flush()
                change_id = change.id
                db.commit()
            except IntegrityError:
                db.rollback(); raise HTTPException(409,'此预览已被认领，请查看变更历史') from None
            except SQLAlchemyError:
                db.rollback(); raise HTTPException(503,'无法记录变更，未执行配置操作') from None
            try:
                result=manager.apply_change(filename,after)
            except (NginxManagerError, OSError):
                # The durable pending row survives an unexpected filesystem failure.
                raise HTTPException(503,dict(message='文件操作异常，需要人工核查',change_id=change_id,status='manual_review_required')) from None
            change.status=result.status; change.steps=result.steps; change.message=result.message
            if result.status in ('applied','simulated'):
                if claims.operation=='delete': db.delete(row); row=None
                else:
                    if row is None:
                        row=SiteConfig(user_id=user_id); db.add(row)
                    for key,value in config.model_dump().items(): setattr(row,key,value)
                    row.config_filename=filename
                    row.status='simulated' if result.status=='simulated' else ('active' if config.enabled else 'draft')
            try:
                db.flush()
                if row is not None: change.site_id=row.id
                db.commit()
                response=dict(change=SiteChangeItem.model_validate(change),site=SiteItem.model_validate(row) if row else None)
            except SQLAlchemyError:
                db.rollback()
                raise HTTPException(503,dict(message='最终数据库记录未确认，需要人工核查配置、运行态与 pending 历史',change_id=change_id,status='manual_review_required')) from None
            if result.status not in ('applied','simulated'):
                raise HTTPException(503,dict(message=result.message,change_id=change_id,status=result.status))
            return response
    except NginxManagerError as exc:
        raise HTTPException(503,exc.detail) from None
