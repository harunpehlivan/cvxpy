import cvxpy.settings as s
import cvxpy.interface.matrix_utilities as intf
from cvxpy.expressions.expression import Expression
from cvxpy.expressions.variable import Variable
from cvxpy.constraints.constraint import EqConstraint, LeqConstraint
from cvxpy.constraints.second_order import SOC

import cvxopt.solvers

class Problem(object):
    """
    An optimization problem.
    """
    # objective - the problem objective.
    # constraints - the problem constraints.
    # target_matrix - the matrix type used internally.
    def __init__(self, objective, constraints=[], interface=None):
        self.objective = objective
        self.constraints = constraints
        self.interface = intf.get_matrix_interface(interface)

    # Does the problem satisfy DCP rules?
    def is_dcp(self):
        return all(exp.is_dcp() for exp in self.constraints + [self.objective])

    # Divide the constraints into separate types.
    def filter_constraints(self, constraints):
        eq_constraints = [c for c in constraints if isinstance(c, EqConstraint)]
        ineq_constraints = [c for c in constraints if isinstance(c, LeqConstraint)]
        soc_constraints = [c for c in constraints if isinstance(c, SOC)]
        return (eq_constraints, ineq_constraints, soc_constraints)

    # Convert the problem into an affine objective and affine constraints.
    # Also returns the dimensions of the cones for the solver.
    def canonicalize(self):
        obj,constraints = self.objective.canonicalize()
        for constr in self.constraints:
            constraints += constr.canonicalize()[1]
        eq_constr,ineq_constr,soc_constr = self.filter_constraints(constraints)
        dims = {'l': sum(c.size()[0]*c.size()[1] for c in ineq_constr)}
        # Formats SOC constraints for the solver.
        for constr in soc_constr:
            ineq_constr += constr.format()
        dims['q'] = [c.size() for c in soc_constr]
        dims['s'] = []
        return (obj,eq_constr,ineq_constr,dims)

    # Solves the problem and returns the value of the objective.
    # Saves the values of variables.
    def solve(self):
        if not self.is_dcp():
            print "Problem does not follow DCP rules."
        objective,eq_constr,ineq_constr,dims = self.canonicalize()
        variables = self.variables(objective, eq_constr + ineq_constr)

        c,null = self.constraints_matrix([objective], variables)
        A,b = self.constraints_matrix(eq_constr, variables)
        G,h = self.constraints_matrix(ineq_constr, variables)

        results = cvxopt.solvers.conelp(c.T,G,h,A=A,b=b,dims=dims)
        if results['status'] == 'optimal':
            self.save_values(results['x'], variables)
            return self.objective.value(results)
        else:
            return results['status']

    # A list of variable name and object, sorted alphabetically.
    def variables(self, objective, constraints):
        vars = objective.variables()
        for constr in constraints:
            vars = dict(vars.items() + constr.variables().items())
        names = vars.keys()
        names.sort()
        return [(k,vars[k]) for k in names]

    # Saves the values of the optimal variables 
    # as fields in the variable objects.
    def save_values(self, result_vec, variables):
        offset = 0
        for (name,var) in variables:
            var.value = self.interface.zeros(*var.size())
            self.interface.block_copy(var.value, 
                                      result_vec[offset:offset+var.rows*var.cols], 
                                      0, 0, var.rows, var.cols)
            # Handle scalars
            if var.size() == (1,1):
                var.value = var.value[0,0]
            offset += var.rows*var.cols

    # Returns a matrix where each variable coefficient is inserted as a block
    # with upper left corner at matrix[variable offset, constraint offset].
    # Also returns a vector representing the constant value associated
    # with the matrix by variables product.
    def constraints_matrix(self, aff_expressions, variables):
        rows = sum([aff.size()[0]*aff.size()[1] for aff in aff_expressions])
        cols = sum([obj.size()[0]*obj.size()[1] for (name,obj) in variables])
        matrix = self.interface.zeros(rows,cols)
        constant_vec = self.interface.zeros(rows,1)
        vert_offset = 0
        for aff_exp in aff_expressions:
            coefficients = aff_exp.coefficients(self.interface)
            horiz_offset = 0
            for (name, obj) in variables:
                for i in range(obj.size()[1]):
                    if name in coefficients:
                        # Update the matrix.
                        self.interface.block_copy(matrix, 
                                                  coefficients[name],
                                                  vert_offset + i*aff_exp.size()[0],
                                                  horiz_offset,
                                                  aff_exp.size()[0],
                                                  obj.size()[0])
                    horiz_offset += obj.size()[0]
            # Update the constants vector.
            self.interface.block_copy(constant_vec, 
                                      Expression.constant(coefficients),
                                      vert_offset, 0,
                                      aff_exp.size()[0] * aff_exp.size()[1], 1)
            vert_offset += aff_exp.size()[0] * aff_exp.size()[1]
        return (matrix,-constant_vec)