from flask import Blueprint, render_template, g, flash,request,url_for,json
from flask_login import current_user
from appli import app,ObjectToStr,PrintInCharte,database,gvg,gvp,user_datastore,DecodeEqualList,XSSEscape
from pathlib import Path
from flask_security import Security, SQLAlchemyUserDatastore
from flask_security import login_required
from flask_security.decorators import roles_accepted
from appli.search.leftfilters import getcommonfilters
import os,time,math,collections,appli,appli.project.sharedfilter as sharedfilter
from appli.database import GetAll,GetClassifQualClass,ExecSQL,db,GetAssoc2Col
import appli.project.main


def GetFieldList(Prj):
    fieldlist = []
    MapList = (('f', 'mappingobj'), ('s', 'mappingsample'), ('a', 'mappingacq'), ('p', 'mappingprocess'))
    MapPrefix =  {'f': 'object.', 's': 'sample.', 'a': 'acquis.', 'p': 'process.'}
    for k in db.metadata.tables['obj_head'].columns.keys():
        if k not in ('objid','projid','img0id','imgcount'):
            fieldlist.append({'id': 'h' + k, 'text': 'object.'+ k})
    fieldlist.append({'id': 'forig_id', 'text': 'object.orig_id'})
    fieldlist.append({'id': 'fobject_link', 'text': 'object.object_link'})
    fieldlist.append({'id': 'sdataportal_descriptor', 'text': 'sample.dataportal_descriptor'})
    fieldlist.append({'id': 'ainstrument', 'text': 'acquis.instrument (fixed)'})
    for mapk, mapv in MapList:
        for k, v in sorted(DecodeEqualList(getattr(Prj, mapv, "")).items(), key=lambda t: t[1]):
            fieldlist.append({'id': mapk + k, 'text': MapPrefix[mapk] + v})
    return fieldlist



######################################################################################################################
@app.route('/prj/editdatamass/<int:PrjId>', methods=['GET', 'POST'])
@login_required
def PrjEditDataMass(PrjId):
    request.form  # Force la lecture des données POST sinon il y a une erreur 504
    Prj=database.Projects.query.filter_by(projid=PrjId).first()
    if Prj is None:
        flash("Project doesn't exists",'error')
        return PrintInCharte("<a href=/prj/>Select another project</a>")
    if not Prj.CheckRight(2): # Level 0 = Read, 1 = Annotate, 2 = Admin
        flash('You cannot edit settings for this project','error')
        return PrintInCharte("<a href=/prj/>Select another project</a>")
    g.headcenter="<h4><a href='/prj/{0}'>{1}</a></h4>".format(Prj.projid,XSSEscape(Prj.title))
    txt = "<h3>Project Mass data edition </h3>"
    sqlparam = {}
    filtres = {}
    for k in sharedfilter.FilterList:
        if gvg(k):
            filtres[k] = gvg(k, "")
    field=gvp('field')
    if field and gvp('newvalue') :
        tables={'f': 'obj_field','h': 'obj_head', 's': 'samples', 'a': 'acquisitions', 'p': 'process'}
        tablecode=field[0]
        table=tables[tablecode] # on extrait la table à partir de la premiere lettre de field
        field=field[1:] # on supprime la premiere lettre qui contenait le nom de la table
        sql="update "+table+" set "+field+"=%(newvalue)s  "
        if field == 'classif_id':
            sql += " ,classif_when=current_timestamp,classif_who="+str(current_user.id)
        sql += " where "
        if tablecode == "h": sql+=" objid in ( select objid from objects o "
        elif tablecode == "f": sql+=" objfid in ( select objid from objects o "
        elif tablecode == "s" : sql+=" sampleid in ( select distinct sampleid from objects o "
        elif tablecode == "a" : sql += " acquisid in ( select distinct acquisid from objects o "
        elif tablecode == "p":  sql += " processid in ( select distinct processid from objects o "
        sql += "  where projid=" + str(Prj.projid)
        sqlparam['newvalue']=gvp('newvalue')
        if len(filtres):
            sql += " "+sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id))
        sql += ")"
        if field=='classif_id':
            sqlhisto="""insert into objectsclassifhisto(objid,classif_date,classif_type,classif_id,classif_qual,classif_who)
                          select objid,classif_when,'M', classif_id,classif_qual,classif_who
                            from objects o
                            where projid=""" + str(Prj.projid) +" and classif_when is not null "
            sqlhisto+=sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id))
            ExecSQL(sqlhisto, sqlparam)
        ExecSQL(sql,sqlparam)
        flash('Data updated', 'success')
    if field=='latitude' or field=='longitude' or gvp('recompute')=='Y':
        ExecSQL("""update samples s set latitude=sll.latitude,longitude=sll.longitude
              from (select o.sampleid,min(o.latitude) latitude,min(o.longitude) longitude
              from obj_head o
              where projid=%(projid)s and o.latitude is not null and o.longitude is not null
              group by o.sampleid) sll where s.sampleid=sll.sampleid and projid=%(projid)s """,{'projid':Prj.projid})
        flash('sample latitude and longitude updated', 'success')
    sql = "select objid FROM objects o where projid=" + str(Prj.projid)
    if len(filtres):
        sql += sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id))
        ObjList = GetAll(sql, sqlparam)
        ObjListTxt = "\n".join((str(r['objid']) for r in ObjList))
        txt += "<span style='color:red;font-weight:bold;font-size:large;'>USING Active Project Filters, {0} objects</span>".format(
            len(ObjList))
    else:
        txt += "<span style='color:red;font-weight:bold;font-size:large;'>Apply to ALL OBJETS OF THE PROJECT (NO Active Filters)</span>"
    Lst=GetFieldList(Prj)
    # txt+="%s"%(Lst,)

    return PrintInCharte(render_template("project/prjeditdatamass.html",Lst=Lst,header=txt))

######################################################################################################################
@app.route('/prj/resettopredicted/<int:PrjId>', methods=['GET', 'POST'])
@login_required
def PrjResetToPredicted(PrjId):
    request.form  # Force la lecture des données POST sinon il y a une erreur 504
    Prj=database.Projects.query.filter_by(projid=PrjId).first()
    if Prj is None:
        flash("Project doesn't exists",'error')
        return PrintInCharte("<a href=/prj/>Select another project</a>")
    if not Prj.CheckRight(2): # Level 0 = Read, 1 = Annotate, 2 = Admin
        flash('You cannot edit settings for this project','error')
        return PrintInCharte("<a href=/prj/>Select another project</a>")
    g.headcenter="<h4><a href='/prj/{0}'>{1}</a></h4>".format(Prj.projid,XSSEscape(Prj.title))
    txt = "<h3>Reset status to predicted</h3>"
    sqlparam = {}
    filtres = {}
    for k in sharedfilter.FilterList:
        if gvg(k):
            filtres[k] = gvg(k, "")
    process=gvp('process')
    if process=='Y':
        sqlhisto="""insert into objectsclassifhisto(objid,classif_date,classif_type,classif_id,classif_qual,classif_who)
                      select objid,classif_when,'M', classif_id,classif_qual,classif_who
                        from objects o
                        where projid=""" + str(Prj.projid) +""" and classif_when is not null and classif_qual in ('V','D')
                         and not exists(select 1 from objectsclassifhisto och where och.objid=o.objid and och.classif_date=o.classif_when)
                        """
        sqlhisto+=sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id))
        ExecSQL(sqlhisto, sqlparam)

        sqlhisto = """update obj_head set classif_qual='P'
                        where projid={0} and objid in (select objid from objects o   
                                              where projid={0} and classif_qual in ('V','D') {1})
                                              """.format(Prj.projid,sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id)))
        ExecSQL(sqlhisto, sqlparam)

        # flash('Data updated', 'success')
        txt+="<a href='/prj/%s' class='btn btn-primary'>Back to project</a> "%(Prj.projid)
        appli.project.main.RecalcProjectTaxoStat(Prj.projid)
        appli.project.main.UpdateProjectStat(Prj.projid)
        return PrintInCharte(txt)
    sql = "select objid FROM objects o where projid=" + str(Prj.projid)
    if len(filtres):
        sql += sharedfilter.GetSQLFilter(filtres, sqlparam, str(current_user.id))
        ObjList = GetAll(sql, sqlparam)
        ObjListTxt = "\n".join((str(r['objid']) for r in ObjList))
        txt += "<span style='color:red;font-weight:bold;font-size:large;'>USING Active Project Filters, {0} objects</span>".format(
            len(ObjList))
    else:
        txt += "<span style='color:red;font-weight:bold;font-size:large;'>Apply to ALL OBJETS OF THE PROJECT (NO Active Filters)</span>"
    Lst=GetFieldList(Prj)
    # txt+="%s"%(Lst,)

    return PrintInCharte(render_template("project/prjresettopredicted.html",Lst=Lst,header=txt))